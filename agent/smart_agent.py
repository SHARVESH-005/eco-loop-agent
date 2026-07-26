"""
Eco-Loop Building Agent -- Smart Rule-Based Agent (Fallback)
Implements the same control logic as the LLM agent using deterministic rules.
Used when API keys are unavailable. Architecture is identical -- tools are still called.
"""
import json
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger("smart_agent")


class SmartRuleAgent:
    """
    Deterministic building optimization agent that mimics LLM behavior.
    Calls the exact same MCP tools as the Gemini agent.
    Implements: setback, daylight harvesting, pre-cooling, demand response.
    """

    def __init__(self, tool_functions: dict):
        self.tool_functions = tool_functions
        self.call_count = 0
        self.total_tool_calls = 0

    def evaluate(self, prompt: str, max_tool_rounds: int = 5) -> dict:
        """Evaluate building state and take control actions using rules."""
        self.call_count += 1
        tool_calls_made = []
        reasoning_parts = []

        # Step 1: Read sensors
        sensor_result = self._call_tool("read_building_sensors", {"zone_name": "all"}, tool_calls_made)
        sensor_data = json.loads(sensor_result) if sensor_result else {}

        if "error" in sensor_data:
            return {"response": "No sensor data available yet.", "tool_calls": tool_calls_made, "actions": []}

        hour = sensor_data.get("hour_of_day", sensor_data.get("hour", 0) % 24)
        weather = sensor_data.get("weather", {})
        zones = sensor_data.get("zones", {})
        outdoor_temp = weather.get("outdoor_temp", 25)
        solar = weather.get("solar_global", 0)

        # Step 2: Check carbon intensity
        carbon_result = self._call_tool("get_grid_carbon_intensity", {"hour": hour}, tool_calls_made)
        carbon_data = json.loads(carbon_result) if carbon_result else {}
        carbon_level = carbon_data.get("level", "medium")
        carbon_intensity = carbon_data.get("carbon_intensity_gco2_kwh", 400)

        # Step 3: Get weather forecast
        forecast_result = self._call_tool("get_weather_forecast", {"hours_ahead": 6}, tool_calls_made)

        # Step 4: Determine occupancy period
        occupancy = config.OCCUPANCY_SCHEDULE.get(hour, 0)
        is_occupied = occupancy > 0.3
        is_peak_occupied = occupancy > 0.8

        # Step 5: Analyze each zone and make decisions
        for zone_name, zdata in zones.items():
            temp = zdata.get("temperature", 23)
            pmv = zdata.get("pmv", 0)
            hvac_mode = zdata.get("hvac_mode", "off")
            zone_solar = zdata.get("solar_gain_w", 0)

            # --- THERMOSTAT STRATEGY ---
            if is_occupied:
                # Occupied hours: optimize within comfort bounds
                if carbon_level == "high" and pmv < 0.3:
                    # High carbon + comfortable: widen cooling setpoint to save energy
                    heating_sp = 20.0
                    cooling_sp = 26.0
                    reasoning_parts.append(f"{zone_name}: High carbon intensity ({carbon_intensity} gCO2/kWh), widening deadband to 20-26C")
                elif outdoor_temp > 30:
                    # Very hot outside: slightly aggressive cooling
                    heating_sp = 20.0
                    cooling_sp = 25.0
                    reasoning_parts.append(f"{zone_name}: Hot outdoor ({outdoor_temp}C), cooling setpoint 25C")
                elif outdoor_temp < 20:
                    # Cool outside: can use free cooling
                    heating_sp = 20.0
                    cooling_sp = 27.0
                    reasoning_parts.append(f"{zone_name}: Cool outdoor ({outdoor_temp}C), widening to 27C for free cooling")
                else:
                    # Normal conditions: moderate optimization
                    heating_sp = 20.0
                    cooling_sp = 25.5
                    reasoning_parts.append(f"{zone_name}: Normal conditions, setpoints 20-25.5C")

                # Zone-specific adjustments based on orientation
                if "South" in zone_name or "West" in zone_name:
                    if solar > 400:
                        # High solar gain zones - pre-accept slightly warmer
                        cooling_sp = min(cooling_sp + 0.5, 27.0)
                        reasoning_parts.append(f"  -> {zone_name} has high solar gain ({zone_solar:.0f}W), allowing +0.5C")
                elif "North" in zone_name:
                    if outdoor_temp < 22:
                        heating_sp = max(heating_sp - 0.5, 19.0)
                        reasoning_parts.append(f"  -> {zone_name} north exposure, reducing heating to {heating_sp}C")
            else:
                # Unoccupied hours: aggressive setback
                heating_sp = 18.0
                cooling_sp = 28.0
                reasoning_parts.append(f"{zone_name}: Unoccupied (hour {hour}), setback to 18-28C")

                # Pre-cooling: if next 3 hours will be occupied and it's currently cheap energy
                if hour in [5, 6] and carbon_level in ["low", "medium"]:
                    cooling_sp = 23.0
                    reasoning_parts.append(f"  -> Pre-cooling before occupancy with clean energy")

            self._call_tool("set_thermostat_setpoint", {
                "zone_name": zone_name,
                "heating_setpoint": heating_sp,
                "cooling_setpoint": cooling_sp,
            }, tool_calls_made)

            # --- LIGHTING STRATEGY ---
            if is_occupied:
                if ("South" in zone_name or "West" in zone_name) and solar > 500:
                    # Daylight harvesting in south/west zones during sunny hours
                    light_frac = 0.4
                    reasoning_parts.append(f"  -> Daylight harvesting: {zone_name} lighting at 40%")
                elif ("East" in zone_name) and solar > 400 and hour < 12:
                    light_frac = 0.5
                    reasoning_parts.append(f"  -> Morning daylight: {zone_name} lighting at 50%")
                elif "Core" in zone_name:
                    light_frac = 0.9  # Core always needs more light
                    reasoning_parts.append(f"  -> Core zone: lighting at 90% (no windows)")
                elif occupancy < 0.5:
                    light_frac = 0.5
                    reasoning_parts.append(f"  -> Low occupancy ({occupancy*100:.0f}%): lighting at 50%")
                else:
                    light_frac = 0.8
                    reasoning_parts.append(f"  -> Standard occupied: lighting at 80%")
            else:
                light_frac = 0.1  # Minimal security lighting
                reasoning_parts.append(f"  -> Unoccupied: lighting at 10%")

            self._call_tool("adjust_lighting_schedule", {
                "zone_name": zone_name,
                "fraction": light_frac,
            }, tool_calls_made)

        # Step 6: Log the decision
        action_summary = f"Hour {hour}: {'Occupied' if is_occupied else 'Unoccupied'} mode. " \
                         f"Carbon: {carbon_level}. Outdoor: {outdoor_temp}C. Solar: {solar:.0f} W/m2."

        if is_occupied:
            if carbon_level == "high":
                strategy = "Demand response: widened deadband during high carbon period"
            elif solar > 500:
                strategy = "Daylight harvesting + moderate setpoint optimization"
            else:
                strategy = "Standard comfort-optimized operation"
        else:
            if hour in [5, 6]:
                strategy = "Pre-cooling with clean overnight energy"
            else:
                strategy = "Aggressive setback during unoccupied hours"

        expected_impact = f"Estimated 15-25% HVAC savings vs baseline through {strategy.lower()}"

        self._call_tool("log_decision", {
            "reasoning": "; ".join(reasoning_parts[:5]),
            "action": action_summary + " Strategy: " + strategy,
            "expected_impact": expected_impact,
        }, tool_calls_made)

        # Build response text
        response = f"[SmartAgent] Hour {hour} | {strategy}\n"
        response += f"Outdoor: {outdoor_temp}C | Carbon: {carbon_level} ({carbon_intensity} gCO2/kWh)\n"
        response += f"Actions: {len(tool_calls_made)} tool calls executed\n"
        for r in reasoning_parts[:3]:
            response += f"  - {r}\n"

        return {
            "response": response,
            "tool_calls": tool_calls_made,
            "actions": [],
        }

    def _call_tool(self, name: str, args: dict, tool_calls_made: list) -> str:
        """Execute a tool and track the call."""
        self.total_tool_calls += 1
        func = self.tool_functions.get(name)
        if func:
            try:
                result = func(**args)
                tool_calls_made.append({
                    "function": name,
                    "arguments": args,
                    "result_preview": result[:200] if isinstance(result, str) else str(result)[:200],
                })
                return result
            except Exception as e:
                logger.error(f"Tool {name} failed: {e}")
                return json.dumps({"error": str(e)})
        return json.dumps({"error": f"Unknown tool: {name}"})

    def reset_conversation(self):
        """No-op for rule-based agent."""
        pass
