"""
Eco-Loop Building Agent — MCP Server
FastMCP server exposing building control tools to the LLM agent.
"""
import json
import sys
import os
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# Use stderr for logging (critical for MCP stdio transport)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("mcp_server")

# ─────────────────────────────────────────────
# Shared State (populated by the orchestrator)
# ─────────────────────────────────────────────
_current_sensor_data = {}
_pending_actions = []
_decision_log = []
_weather_forecast = {}


def update_sensor_data(data: dict):
    """Called by orchestrator to push new sensor data."""
    global _current_sensor_data
    _current_sensor_data = data


def get_pending_actions() -> list:
    """Called by orchestrator to retrieve AI control actions."""
    global _pending_actions
    actions = _pending_actions.copy()
    _pending_actions.clear()
    return actions


def get_decision_log() -> list:
    """Get all logged decisions."""
    return _decision_log.copy()


def update_weather_forecast(forecast: dict):
    """Update weather forecast data."""
    global _weather_forecast
    _weather_forecast = forecast


# ─────────────────────────────────────────────
# Tool Functions (used by Gemini tool-calling)
# ─────────────────────────────────────────────

def read_building_sensors(zone_name: str = "all") -> str:
    """
    Read current sensor data from the building simulation.
    Returns zone temperatures, humidity, energy consumption, PMV comfort index.
    
    Args:
        zone_name: Specific zone name or 'all' for all zones
    """
    if not _current_sensor_data:
        return json.dumps({"error": "No sensor data available yet"})
    
    if zone_name == "all":
        return json.dumps(_current_sensor_data, indent=2)
    
    zones = _current_sensor_data.get("zones", {})
    if zone_name in zones:
        return json.dumps({
            "zone": zone_name,
            "data": zones[zone_name],
            "weather": _current_sensor_data.get("weather", {}),
            "hour": _current_sensor_data.get("hour", 0),
        }, indent=2)
    
    return json.dumps({"error": f"Zone '{zone_name}' not found. Available: {list(zones.keys())}"})


def calculate_pmv(temperature: float, humidity: float, air_speed: float = 0.1,
                  met_rate: float = 1.1, clo_value: float = 0.7) -> str:
    """
    Calculate PMV (Predicted Mean Vote) thermal comfort index.
    PMV range: -3 (cold) to +3 (hot). Target: -0.5 to +0.5.
    
    Args:
        temperature: Zone air temperature in °C
        humidity: Relative humidity in %
        air_speed: Air velocity in m/s (default 0.1)
        met_rate: Metabolic rate in met (default 1.1 for office)
        clo_value: Clothing insulation in clo (default 0.7 for summer)
    """
    try:
        from pythermalcomfort.models import pmv_ppd
        result = pmv_ppd(
            tdb=temperature, tr=temperature + 0.5,
            vr=max(0.1, air_speed), rh=humidity,
            met=met_rate, clo=clo_value, standard="ISO"
        )
        pmv = result["pmv"]
        ppd = result["ppd"]
    except Exception:
        neutral = 23.5
        pmv = 0.33 * (temperature - neutral) + 0.01 * (humidity - 50) * 0.1
        ppd = 100 - 95 * (2.71828 ** (-0.03353 * pmv**4 - 0.2179 * pmv**2))
    
    comfort = "comfortable" if -0.5 <= pmv <= 0.5 else ("too warm" if pmv > 0.5 else "too cold")
    
    return json.dumps({
        "pmv": round(pmv, 2),
        "ppd": round(max(5, ppd), 1),
        "comfort_status": comfort,
        "target_range": "-0.5 to +0.5",
        "recommendation": (
            "No action needed" if comfort == "comfortable"
            else f"{'Lower' if pmv > 0 else 'Raise'} setpoint by {abs(pmv) * 1.5:.1f}°C"
        )
    })


def analyze_energy_pattern(n_steps: int = 6) -> str:
    """
    Analyze energy consumption trends over the last N timesteps.
    Returns trend direction, peak detection, and efficiency insights.
    
    Args:
        n_steps: Number of recent timesteps to analyze (default 6)
    """
    data = _current_sensor_data
    if not data:
        return json.dumps({"error": "No data available"})
    
    history = data.get("history", [])
    recent = history[-n_steps:] if len(history) >= n_steps else history
    
    if len(recent) < 2:
        return json.dumps({"trend": "insufficient_data", "message": "Need at least 2 timesteps"})
    
    energies = [h.get("total_energy_kwh", 0) for h in recent]
    hvac_energies = [h.get("total_hvac_kwh", 0) for h in recent]
    
    trend = "rising" if energies[-1] > energies[0] else "falling" if energies[-1] < energies[0] else "stable"
    avg = sum(energies) / len(energies)
    peak = max(energies)
    
    return json.dumps({
        "trend": trend,
        "avg_total_kwh": round(avg, 2),
        "peak_total_kwh": round(peak, 2),
        "latest_total_kwh": round(energies[-1], 2),
        "avg_hvac_kwh": round(sum(hvac_energies) / len(hvac_energies), 2),
        "hvac_share_pct": round(sum(hvac_energies) / max(sum(energies), 0.001) * 100, 1),
        "n_steps_analyzed": len(recent),
        "insight": (
            f"Energy is {trend}. HVAC accounts for {round(sum(hvac_energies)/max(sum(energies),0.001)*100,1)}% of total. "
            f"{'Consider widening deadband or pre-cooling.' if trend == 'rising' else 'Current strategy is effective.'}"
        )
    })


def set_thermostat_setpoint(zone_name: str, heating_setpoint: float = None,
                             cooling_setpoint: float = None) -> str:
    """
    Set new thermostat heating/cooling setpoints for a zone.
    Must stay within safety limits: heating 18-23°C, cooling 23-28°C.
    
    Args:
        zone_name: Target zone name (e.g., 'Zone_North_Perimeter' or 'all')
        heating_setpoint: New heating setpoint in °C (optional)
        cooling_setpoint: New cooling setpoint in °C (optional)
    """
    global _pending_actions
    
    # Validate bounds
    if heating_setpoint is not None:
        heating_setpoint = max(config.MIN_HEATING_SETPOINT, 
                               min(config.MAX_HEATING_SETPOINT, heating_setpoint))
    if cooling_setpoint is not None:
        cooling_setpoint = max(config.MIN_COOLING_SETPOINT,
                               min(config.MAX_COOLING_SETPOINT, cooling_setpoint))
    
    # Ensure deadband
    if heating_setpoint and cooling_setpoint and cooling_setpoint - heating_setpoint < 1.0:
        cooling_setpoint = heating_setpoint + 1.0
    
    zones = config.ZONE_NAMES if zone_name == "all" else [zone_name]
    
    for z in zones:
        action = {"type": "setpoint", "zone": z}
        if heating_setpoint is not None:
            action["heating_setpoint"] = round(heating_setpoint, 1)
        if cooling_setpoint is not None:
            action["cooling_setpoint"] = round(cooling_setpoint, 1)
        _pending_actions.append(action)
    
    return json.dumps({
        "status": "success",
        "zones_affected": zones,
        "heating_setpoint": heating_setpoint,
        "cooling_setpoint": cooling_setpoint,
        "message": f"Setpoints updated for {len(zones)} zone(s)"
    })


def adjust_lighting_schedule(zone_name: str, fraction: float) -> str:
    """
    Adjust lighting power fraction for a zone based on occupancy/daylight.
    Fraction: 0.0 (off) to 1.0 (full power).
    
    Args:
        zone_name: Target zone name or 'all'
        fraction: Lighting power fraction (0.0 to 1.0)
    """
    global _pending_actions
    
    fraction = max(0.0, min(1.0, fraction))
    zones = config.ZONE_NAMES if zone_name == "all" else [zone_name]
    
    for z in zones:
        _pending_actions.append({
            "type": "lighting",
            "zone": z,
            "lighting_fraction": round(fraction, 2),
        })
    
    return json.dumps({
        "status": "success",
        "zones_affected": zones,
        "lighting_fraction": fraction,
        "estimated_savings_pct": round((1 - fraction) * 100, 1),
    })


def get_grid_carbon_intensity(hour: int) -> str:
    """
    Get the current grid carbon intensity signal (gCO₂/kWh).
    Higher values mean dirtier grid — good time to reduce consumption.
    
    Args:
        hour: Hour of day (0-23)
    """
    hour = hour % 24
    intensity = config.GRID_CARBON_INTENSITY.get(hour, 400)
    
    # Classify
    if intensity < 300:
        level = "low"
        recommendation = "Grid is clean — good time for energy-intensive operations"
    elif intensity < 450:
        level = "medium"
        recommendation = "Moderate carbon intensity — balance needed"
    else:
        level = "high"
        recommendation = "High carbon intensity — minimize energy use, consider pre-cooling/storing"
    
    return json.dumps({
        "hour": hour,
        "carbon_intensity_gco2_kwh": intensity,
        "level": level,
        "recommendation": recommendation,
        "next_low_period": "0:00-5:00" if hour > 5 else "now",
    })


def get_weather_forecast(hours_ahead: int = 6) -> str:
    """
    Get weather forecast for the next N hours.
    Useful for predictive control strategies (pre-cooling, load shifting).
    
    Args:
        hours_ahead: How many hours ahead to forecast (1-24)
    """
    if _weather_forecast:
        forecast = _weather_forecast.get("forecast", [])[:hours_ahead]
        return json.dumps({"forecast": forecast, "hours": len(forecast)})
    
    return json.dumps({"error": "Weather forecast not available"})


def log_decision(reasoning: str, action: str, expected_impact: str) -> str:
    """
    Log the AI agent's decision with reasoning for the audit trail.
    Every control action should be logged with clear justification.
    
    Args:
        reasoning: Why this decision was made (analysis of current conditions)
        action: What action is being taken (setpoint change, schedule update, etc.)
        expected_impact: Expected energy/comfort impact of this action
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "hour": _current_sensor_data.get("hour", "N/A"),
        "reasoning": reasoning,
        "action": action,
        "expected_impact": expected_impact,
    }
    _decision_log.append(entry)
    
    # Also append to log file
    try:
        with open(config.AGENT_DECISIONS_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to write decision log: {e}")
    
    return json.dumps({"status": "logged", "entry_number": len(_decision_log)})


# ─────────────────────────────────────────────
# Tool Registry (for Gemini function declarations)
# ─────────────────────────────────────────────
TOOL_FUNCTIONS = {
    "read_building_sensors": read_building_sensors,
    "calculate_pmv": calculate_pmv,
    "analyze_energy_pattern": analyze_energy_pattern,
    "set_thermostat_setpoint": set_thermostat_setpoint,
    "adjust_lighting_schedule": adjust_lighting_schedule,
    "get_grid_carbon_intensity": get_grid_carbon_intensity,
    "get_weather_forecast": get_weather_forecast,
    "log_decision": log_decision,
}


def execute_tool(tool_name: str, arguments: dict) -> str:
    """Execute a tool by name with given arguments."""
    if tool_name in TOOL_FUNCTIONS:
        try:
            result = TOOL_FUNCTIONS[tool_name](**arguments)
            logger.info(f"Tool '{tool_name}' executed successfully")
            return result
        except Exception as e:
            error_msg = json.dumps({"error": str(e), "tool": tool_name})
            logger.error(f"Tool '{tool_name}' failed: {e}")
            return error_msg
    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
