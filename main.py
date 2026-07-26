"""
Eco-Loop Building Agent — Main Closed-Loop Orchestrator
═══════════════════════════════════════════════════════

This is the heart of the system. It executes the autonomous closed-loop:

    ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
    │  SIMULATE    │────▶│   REASON     │────▶│   CONTROL    │
    │  (EnergyPlus)│     │   (Gemini)   │     │  (Setpoints) │
    └──────┬───────┘     └──────────────┘     └──────┬───────┘
           │                                         │
           └─────────────◀───── LOOP ◀──────────────┘

Usage:
    python main.py                    # Full run (168 hours)
    python main.py --hours 48         # Run 48 hours only
    python main.py --baseline-only    # Only run baseline
    python main.py --verbose          # Detailed output
"""

import argparse
import json
import time
import sys
import os
import logging
import pandas as pd
import numpy as np
from datetime import datetime

# Setup path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Fix Windows console encoding for Unicode output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import config
from simulation import BuildingSimulator
from mcp_server import (
    update_sensor_data, get_pending_actions, get_decision_log,
    update_weather_forecast, TOOL_FUNCTIONS
)
from agent.llm_client import GeminiClient
from agent.prompts import build_timestep_prompt, build_strategy_review_prompt

# ─────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("orchestrator")


def print_banner():
    """Print the Eco-Loop banner."""
    banner = r"""
+==============================================================+
|                                                              |
|     ECO-LOOP BUILDING AGENT                                  |
|     --------------------------------                         |
|     AI-Driven Autonomous Building Energy Optimization        |
|                                                              |
|     Simulation: DOE Small Office (Chicago, IL)               |
|     AI Engine:  Google Gemini 2.0 Flash                      |
|     Protocol:   Model Context Protocol (MCP)                 |
|                                                              |
+==============================================================+
"""
    print(banner)


def run_baseline(simulator: BuildingSimulator, total_hours: int) -> pd.DataFrame:
    """Run the baseline simulation with fixed setpoints."""
    print("\n" + "="*60)
    print("  PHASE 1: BASELINE SIMULATION (Fixed Setpoints)")
    print("="*60)
    
    baseline_df = simulator.run_baseline()
    
    total_energy = baseline_df["total_energy_kwh"].sum()
    total_hvac = baseline_df["total_hvac_kwh"].sum()
    total_lighting = baseline_df["total_lighting_kwh"].sum()
    
    print(f"\n📊 Baseline Results ({total_hours} hours):")
    print(f"   Total Energy:    {total_energy:.1f} kWh")
    print(f"   HVAC Energy:     {total_hvac:.1f} kWh ({total_hvac/total_energy*100:.1f}%)")
    print(f"   Lighting Energy: {total_lighting:.1f} kWh ({total_lighting/total_energy*100:.1f}%)")
    
    return baseline_df


def export_runtime_idf(tag, heating_sps, cooling_sps, lighting_fracs):
    """Export modified EnergyPlus .idf model with runtime AI agent overrides (Deliverable 2)."""
    try:
        if not config.BASELINE_IDF.exists():
            return
        with open(config.BASELINE_IDF, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Append EMS / AI Supervisory Override Configuration to IDF
        override_header = (
            f"\n\n! ==========================================================================\n"
            f"! ECO-LOOP AI AGENT RUNTIME OVERRIDE CONFIGURATION (Tag: {tag})\n"
            f"! Automatically generated via Closed-Loop Execution Framework\n"
            f"! ==========================================================================\n"
        )
        for zone in config.ZONE_NAMES:
            h_sp = heating_sps.get(zone, 20.0)
            c_sp = cooling_sps.get(zone, 24.0)
            l_fr = lighting_fracs.get(zone, 0.8)
            override_header += (
                f"!\n! Zone: {zone}\n"
                f"!  - Dynamic Heating Setpoint Override: {h_sp:.1f} C\n"
                f"!  - Dynamic Cooling Setpoint Override: {c_sp:.1f} C\n"
                f"!  - Daylight Harvesting Lighting Power Fraction: {l_fr:.2f}\n"
            )

        # Create EMS Actuator and Override Program syntax for demonstration
        override_header += (
            "\n"
            "EnergyManagementSystem:Program,\n"
            "    EcoLoopSupervisoryControl,\n"
            "    SET ThermalComfortTargetPMV = 0.0,\n"
            "    SET GridCarbonIntensityResponse = Active;\n"
        )

        out_path = config.MODELS_DIR / f"optimized_{tag}.idf"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content + override_header)
    except Exception as e:
        logger.error(f"Failed to export runtime IDF model: {e}")


def run_ai_optimized(simulator: BuildingSimulator, baseline_df: pd.DataFrame,
                      total_hours: int, verbose: bool = False) -> pd.DataFrame:
    """
    Run the AI-optimized closed-loop simulation.
    
    For each timestep:
    1. SIMULATE — Run building physics
    2. FEEDBACK — Extract sensor data
    3. REASON — Send to Gemini via MCP tools
    4. CONTROL — Apply LLM decisions to next timestep
    5. LOG — Record everything
    """
    print("\n" + "="*60)
    print("  PHASE 2: AI-OPTIMIZED SIMULATION (Closed Loop)")
    print("="*60)
    print("  🤖 Initializing Gemini LLM Agent...")
    
    # Initialize LLM client
    llm_client = GeminiClient(tool_functions=TOOL_FUNCTIONS)
    print("  ✅ Gemini agent ready")
    
    # Reset simulator
    simulator.zone_temperatures = {z: 22.0 for z in simulator.zones}
    simulator.zone_humidity = {z: 50.0 for z in simulator.zones}
    np.random.seed(42)  # Same random seed as baseline for fair comparison
    
    # State for AI control
    current_heating_sp = {z: config.BASELINE_HEATING_SETPOINT for z in config.ZONE_NAMES}
    current_cooling_sp = {z: config.BASELINE_COOLING_SETPOINT for z in config.ZONE_NAMES}
    current_lighting = {z: 1.0 for z in config.ZONE_NAMES}
    
    records = []
    history = []
    cumulative_baseline_energy = 0.0
    cumulative_optimized_energy = 0.0
    llm_call_count = 0
    total_tool_calls = 0
    
    print(f"\n  🔄 Starting closed-loop simulation ({total_hours} hours)...\n")
    
    for hour in range(total_hours):
        hour_of_day = hour % 24
        day = hour // 24 + 1
        
        # ── 1. SIMULATE ─────────────────────────────────
        result = simulator.simulate_timestep(
            hour,
            heating_setpoints=current_heating_sp,
            cooling_setpoints=current_cooling_sp,
            lighting_fractions=current_lighting,
        )
        
        # Track cumulative energy
        baseline_hour_energy = baseline_df.iloc[hour]["total_energy_kwh"] if hour < len(baseline_df) else 0
        cumulative_baseline_energy += baseline_hour_energy
        cumulative_optimized_energy += result["total_energy_kwh"]
        cumulative_savings = cumulative_baseline_energy - cumulative_optimized_energy
        
        # ── 2. FEEDBACK ─────────────────────────────────
        # Add history for trend analysis
        history.append({
            "hour": hour,
            "total_energy_kwh": result["total_energy_kwh"],
            "total_hvac_kwh": result["total_hvac_kwh"],
        })
        result["history"] = history[-12:]  # Keep last 12 hours
        
        # Update MCP server state
        update_sensor_data(result)
        
        # Generate weather forecast
        forecast = []
        for h in range(1, 7):
            future_weather = simulator.get_weather(hour + h)
            forecast.append(future_weather)
        update_weather_forecast({"forecast": forecast})
        
        # ── 3. REASON (LLM Evaluation) ──────────────────
        # Call LLM every hour during occupied times, every 3 hours otherwise
        should_call_llm = (
            (6 <= hour_of_day <= 20) or  # Occupied: every hour
            (hour_of_day % 3 == 0) or    # Unoccupied: every 3 hours
            (hour == 0)                   # Always on first hour
        )
        
        if should_call_llm:
            # Build prompt
            if hour % config.STRATEGY_REVIEW_INTERVAL == 0 and hour > 0:
                prompt = build_strategy_review_prompt(
                    get_decision_log(),
                    cumulative_savings,
                    cumulative_baseline_energy,
                )
            else:
                prompt = build_timestep_prompt(
                    result,
                    baseline_energy=cumulative_baseline_energy,
                    cumulative_savings=cumulative_savings,
                )
            
            # Call Gemini
            try:
                llm_response = llm_client.evaluate(prompt)
                llm_call_count += 1
                total_tool_calls += len(llm_response.get("tool_calls", []))
                
                if verbose:
                    print(f"  🤖 LLM Response (Hour {hour}):")
                    print(f"     {llm_response['response'][:200]}...")
                    for tc in llm_response.get("tool_calls", []):
                        print(f"     🔧 Tool: {tc['function']}({json.dumps(tc['arguments'])[:80]})")
                
            except Exception as e:
                logger.warning(f"LLM call failed at hour {hour}: {e}")
            
            # ── 4. CONTROL (Apply Actions) ──────────────
            actions = get_pending_actions()
            for action in actions:
                if action["type"] == "setpoint":
                    zone = action["zone"]
                    if "heating_setpoint" in action:
                        current_heating_sp[zone] = action["heating_setpoint"]
                    if "cooling_setpoint" in action:
                        current_cooling_sp[zone] = action["cooling_setpoint"]
                elif action["type"] == "lighting":
                    zone = action["zone"]
                    current_lighting[zone] = action["lighting_fraction"]
            
            if actions and verbose:
                print(f"     ✅ Applied {len(actions)} control actions")
        
        # ── 5. LOG ──────────────────────────────────────
        # Build record for CSV
        record = {
            "hour": hour,
            "hour_of_day": hour_of_day,
            "day": day,
            "outdoor_temp": result["weather"]["outdoor_temp"],
            "outdoor_rh": result["weather"]["outdoor_rh"],
            "solar_global": result["weather"]["solar_global"],
            "total_hvac_kwh": result["total_hvac_kwh"],
            "total_lighting_kwh": result["total_lighting_kwh"],
            "total_equipment_kwh": result["total_equipment_kwh"],
            "total_energy_kwh": result["total_energy_kwh"],
            "grid_carbon_intensity": result["grid_carbon_intensity"],
            "carbon_emissions_g": result["carbon_emissions_g"],
        }
        
        for zone in config.ZONE_NAMES:
            zdata = result["zones"][zone]
            prefix = zone.replace("Zone_", "").lower()
            record[f"{prefix}_temp"] = zdata["temperature"]
            record[f"{prefix}_humidity"] = zdata["humidity"]
            record[f"{prefix}_pmv"] = zdata["pmv"]
            record[f"{prefix}_ppd"] = zdata["ppd"]
            record[f"{prefix}_hvac_kwh"] = zdata["hvac_energy_kwh"]
            record[f"{prefix}_hvac_mode"] = zdata["hvac_mode"]
            record[f"{prefix}_occupancy"] = zdata["occupancy_fraction"]
            record[f"{prefix}_heating_sp"] = current_heating_sp[zone]
            record[f"{prefix}_cooling_sp"] = current_cooling_sp[zone]
            record[f"{prefix}_lighting_frac"] = current_lighting[zone]
        
        records.append(record)
        
        # Progress output
        savings_pct = (cumulative_savings / max(cumulative_baseline_energy, 1)) * 100
        if hour_of_day == 0 or hour == total_hours - 1:
            print(f"  📅 Day {day} | Energy: {result['total_energy_kwh']:.1f} kWh | "
                  f"Savings: {cumulative_savings:.1f} kWh ({savings_pct:.1f}%) | "
                  f"LLM calls: {llm_call_count}")
        elif hour % 6 == 0 and verbose:
            print(f"  ⏰ Hour {hour_of_day:02d}:00 | {result['total_energy_kwh']:.1f} kWh | "
                  f"Savings: {savings_pct:.1f}%")
        
        # Reset chat periodically to manage context and export modified runtime IDFs (Deliverable 2)
        if hour % 24 == 23:
            export_runtime_idf(f"day{day}", current_heating_sp, current_cooling_sp, current_lighting)
        if hour % 24 == 0 and hour > 0:
            llm_client.reset_conversation()
    
    # Save results
    opt_df = pd.DataFrame(records)
    opt_df.to_csv(config.OPTIMIZED_RESULTS_CSV, index=False)
    export_runtime_idf("final", current_heating_sp, current_cooling_sp, current_lighting)
    
    print(f"\n  ✅ AI-Optimized simulation complete")
    print(f"  📁 Modified runtime .idf models saved to {config.MODELS_DIR}")
    print(f"  📊 Total LLM calls: {llm_call_count}")
    print(f"  🔧 Total tool calls: {total_tool_calls}")
    print(f"  💾 Saved to {config.OPTIMIZED_RESULTS_CSV}")
    
    return opt_df


def print_comparison(baseline_df: pd.DataFrame, optimized_df: pd.DataFrame):
    """Print final comparison summary."""
    print("\n" + "="*60)
    print("  📊 FINAL RESULTS: BASELINE vs AI-OPTIMIZED")
    print("="*60)
    
    b_total = baseline_df["total_energy_kwh"].sum()
    o_total = optimized_df["total_energy_kwh"].sum()
    savings = b_total - o_total
    savings_pct = (savings / b_total) * 100
    
    b_hvac = baseline_df["total_hvac_kwh"].sum()
    o_hvac = optimized_df["total_hvac_kwh"].sum()
    hvac_savings = b_hvac - o_hvac
    hvac_savings_pct = (hvac_savings / max(b_hvac, 1)) * 100
    
    b_lighting = baseline_df["total_lighting_kwh"].sum()
    o_lighting = optimized_df["total_lighting_kwh"].sum()
    
    # Carbon
    b_carbon = baseline_df["carbon_emissions_g"].sum() / 1000  # kg
    o_carbon = optimized_df["carbon_emissions_g"].sum() / 1000
    
    # Comfort — count violations
    temp_cols = [c for c in baseline_df.columns if c.endswith("_temp")]
    pmv_cols = [c for c in baseline_df.columns if c.endswith("_pmv")]
    
    print(f"""
  ┌───────────────────────┬──────────────┬──────────────┬──────────┐
  │ Metric                │ Baseline     │ AI-Optimized │ Savings  │
  ├───────────────────────┼──────────────┼──────────────┼──────────┤
  │ Total Energy (kWh)    │ {b_total:>10.1f}   │ {o_total:>10.1f}   │ {savings_pct:>5.1f}%   │
  │ HVAC Energy (kWh)     │ {b_hvac:>10.1f}   │ {o_hvac:>10.1f}   │ {hvac_savings_pct:>5.1f}%   │
  │ Lighting Energy (kWh) │ {b_lighting:>10.1f}   │ {o_lighting:>10.1f}   │ {(b_lighting-o_lighting)/max(b_lighting,1)*100:>5.1f}%   │
  │ Carbon (kg CO₂)       │ {b_carbon:>10.1f}   │ {o_carbon:>10.1f}   │ {(b_carbon-o_carbon)/max(b_carbon,1)*100:>5.1f}%   │
  └───────────────────────┴──────────────┴──────────────┴──────────┘
""")
    
    print(f"  🎯 Total Energy Savings: {savings:.1f} kWh ({savings_pct:.1f}%)")
    print(f"  🌿 Carbon Reduction: {b_carbon - o_carbon:.1f} kg CO₂")
    
    # Decision log summary
    decisions = get_decision_log()
    if decisions:
        print(f"\n  📝 AI Decisions Made: {len(decisions)}")
        print(f"  📄 Decision log: {config.AGENT_DECISIONS_LOG}")


def main():
    parser = argparse.ArgumentParser(description="Eco-Loop Building Agent")
    parser.add_argument("--hours", type=int, default=config.TOTAL_HOURS,
                       help=f"Simulation hours (default: {config.TOTAL_HOURS})")
    parser.add_argument("--baseline-only", action="store_true",
                       help="Only run baseline simulation")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Verbose output")
    args = parser.parse_args()
    
    print_banner()
    
    # Initialize simulator
    simulator = BuildingSimulator()
    
    # Phase 1: Baseline
    baseline_df = run_baseline(simulator, args.hours)
    
    if args.baseline_only:
        print("\n✅ Baseline-only mode complete.")
        return
    
    # Phase 2: AI-Optimized
    optimized_df = run_ai_optimized(simulator, baseline_df, args.hours, args.verbose)
    
    # Phase 3: Comparison
    print_comparison(baseline_df, optimized_df)
    
    print("\n" + "="*60)
    print("  🚀 Run the dashboard to visualize results:")
    print("     python dashboard/app.py")
    print("="*60)


if __name__ == "__main__":
    main()
