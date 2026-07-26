"""
Eco-Loop Building Agent -- Gemini LLM Client
Google Gemini API client with automatic tool-calling loop and key rotation.
Uses the new google.genai SDK.
"""
import json
import time
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger("llm_client")

from google import genai
from google.genai import types
from agent.smart_agent import SmartRuleAgent


class GeminiClient:
    """
    Gemini API client with:
    - Automatic API key rotation on exhaustion
    - Tool-calling loop (send -> function_call -> execute -> respond)
    - Conversation history management with sliding window
    - Automatic offline fallback to SmartRuleAgent if API keys are exhausted or invalid
    """

    def __init__(self, tool_functions: dict):
        self.api_keys = list(config.GEMINI_API_KEYS)
        self.current_key_index = 0
        self.tool_functions = tool_functions
        self.history = []  # conversation history as list of Content objects
        self.fallback_agent = None
        self.force_fallback = False

        # Build tool declarations
        self.tools = self._build_tool_declarations()

        # Configure client
        self._configure_client()

    def _configure_client(self):
        """Configure Gemini client with current key."""
        key = self.api_keys[self.current_key_index]
        self.client = genai.Client(api_key=key)
        logger.info(f"Configured Gemini API with key #{self.current_key_index + 1}")

    def _rotate_key(self):
        """Rotate to next API key."""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        self._configure_client()
        logger.warning(f"Rotated to API key #{self.current_key_index + 1}")

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the building agent."""
        from agent.prompts import SYSTEM_PROMPT
        return SYSTEM_PROMPT

    def _build_tool_declarations(self) -> list:
        """Build Gemini function declarations from tool functions."""
        function_declarations = []

        tool_schemas = {
            "read_building_sensors": {
                "description": "Read current sensor data from the building simulation. Returns zone temperatures, humidity, energy consumption, and PMV thermal comfort index.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "zone_name": {
                            "type": "STRING",
                            "description": "Specific zone name (e.g., 'Zone_North_Perimeter') or 'all' for all zones"
                        }
                    },
                    "required": ["zone_name"]
                }
            },
            "calculate_pmv": {
                "description": "Calculate PMV (Predicted Mean Vote) thermal comfort index using ISO 7730. PMV range: -3 (cold) to +3 (hot). Target: -0.5 to +0.5.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "temperature": {"type": "NUMBER", "description": "Zone air temperature in deg C"},
                        "humidity": {"type": "NUMBER", "description": "Relative humidity in percent"},
                        "air_speed": {"type": "NUMBER", "description": "Air velocity in m/s (default 0.1)"},
                        "met_rate": {"type": "NUMBER", "description": "Metabolic rate in met (default 1.1)"},
                        "clo_value": {"type": "NUMBER", "description": "Clothing insulation in clo (default 0.7)"}
                    },
                    "required": ["temperature", "humidity"]
                }
            },
            "analyze_energy_pattern": {
                "description": "Analyze energy consumption trends over the last N timesteps. Returns trend direction, peak values, and efficiency insights.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "n_steps": {"type": "INTEGER", "description": "Number of recent timesteps to analyze (default 6)"}
                    },
                    "required": []
                }
            },
            "set_thermostat_setpoint": {
                "description": "Set new thermostat heating/cooling setpoints for a zone. Safety limits: heating 18-23 deg C, cooling 23-28 deg C.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "zone_name": {"type": "STRING", "description": "Target zone name or 'all'"},
                        "heating_setpoint": {"type": "NUMBER", "description": "New heating setpoint in deg C"},
                        "cooling_setpoint": {"type": "NUMBER", "description": "New cooling setpoint in deg C"}
                    },
                    "required": ["zone_name"]
                }
            },
            "adjust_lighting_schedule": {
                "description": "Adjust lighting power fraction for a zone. 0.0 = off, 1.0 = full power.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "zone_name": {"type": "STRING", "description": "Target zone name or 'all'"},
                        "fraction": {"type": "NUMBER", "description": "Lighting power fraction (0.0 to 1.0)"}
                    },
                    "required": ["zone_name", "fraction"]
                }
            },
            "get_grid_carbon_intensity": {
                "description": "Get current grid carbon intensity in gCO2/kWh. Higher = dirtier grid.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "hour": {"type": "INTEGER", "description": "Hour of day (0-23)"}
                    },
                    "required": ["hour"]
                }
            },
            "get_weather_forecast": {
                "description": "Get weather forecast for upcoming hours for predictive control.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "hours_ahead": {"type": "INTEGER", "description": "Hours ahead to forecast (1-24)"}
                    },
                    "required": ["hours_ahead"]
                }
            },
            "log_decision": {
                "description": "Log your decision with reasoning for the audit trail. ALWAYS call this after making any control action.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "reasoning": {"type": "STRING", "description": "Why this decision was made"},
                        "action": {"type": "STRING", "description": "What action is being taken"},
                        "expected_impact": {"type": "STRING", "description": "Expected energy/comfort impact"}
                    },
                    "required": ["reasoning", "action", "expected_impact"]
                }
            },
        }

        for name, schema in tool_schemas.items():
            function_declarations.append(types.FunctionDeclaration(
                name=name,
                description=schema["description"],
                parameters=schema["parameters"],
            ))

        return [types.Tool(function_declarations=function_declarations)]

    def evaluate(self, prompt: str, max_tool_rounds: int = 5) -> dict:
        """
        Send a prompt to Gemini and handle the tool-calling loop.
        Automatically falls back to SmartRuleAgent if online LLM APIs fail or reject keys.
        """
        if self.force_fallback:
            if not self.fallback_agent:
                self.fallback_agent = SmartRuleAgent(self.tool_functions)
            return self.fallback_agent.evaluate(prompt, max_tool_rounds)

        tool_calls_made = []

        for attempt in range(min(len(self.api_keys) + 1, config.MAX_RETRIES)):
            try:
                return self._execute_with_tools(prompt, max_tool_rounds, tool_calls_made)
            except Exception as e:
                error_str = str(e).lower()
                logger.warning(f"Gemini API issue with key #{self.current_key_index + 1}: {e}")
                
                # Rotate key if there are more keys to try and it's a rate limit or auth issue
                if self.current_key_index < len(self.api_keys) - 1 and ("quota" in error_str or "rate" in error_str or "429" in error_str or "invalid" in error_str or "400" in error_str or "403" in error_str):
                    self._rotate_key()
                else:
                    break

        # Fallback to offline smart optimization agent to ensure continuous loop operation
        logger.warning("All online API keys rejected or unavailable. Seamlessly activating offline SmartRuleAgent for MCP control loop...")
        self.force_fallback = True
        if not self.fallback_agent:
            self.fallback_agent = SmartRuleAgent(self.tool_functions)
        return self.fallback_agent.evaluate(prompt, max_tool_rounds)

    def _execute_with_tools(self, prompt: str, max_rounds: int,
                             tool_calls_made: list) -> dict:
        """Execute the tool-calling loop using google.genai."""
        # Build contents with history
        contents = list(self.history)
        contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)]
        ))

        for round_num in range(max_rounds):
            response = self.client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=self._get_system_prompt(),
                    tools=self.tools,
                    temperature=0.3,
                ),
            )

            # Check if response has function calls
            has_function_call = False
            function_responses = []

            if response.candidates and response.candidates[0].content:
                resp_parts = response.candidates[0].content.parts or []

                for part in resp_parts:
                    if part.function_call:
                        has_function_call = True
                        fc = part.function_call
                        func_name = fc.name
                        func_args = dict(fc.args) if fc.args else {}

                        logger.info(f"  Tool call: {func_name}({json.dumps(func_args)[:100]})")

                        # Execute the tool
                        tool_func = self.tool_functions.get(func_name)
                        if tool_func:
                            try:
                                result = tool_func(**func_args)
                            except Exception as ex:
                                result = json.dumps({"error": str(ex)})
                        else:
                            result = json.dumps({"error": f"Unknown tool: {func_name}"})

                        tool_calls_made.append({
                            "function": func_name,
                            "arguments": func_args,
                            "result_preview": result[:200] if isinstance(result, str) else str(result)[:200],
                        })

                        function_responses.append(types.Part.from_function_response(
                            name=func_name,
                            response={"result": result},
                        ))

                if not has_function_call:
                    # No function calls - extract final text
                    final_text = ""
                    for part in resp_parts:
                        if part.text:
                            final_text += part.text

                    # Update history (keep it minimal)
                    self.history.append(types.Content(role="user",
                        parts=[types.Part.from_text(text=prompt[:500])]))
                    if final_text:
                        self.history.append(types.Content(role="model",
                            parts=[types.Part.from_text(text=final_text[:500])]))

                    # Trim history to keep context manageable
                    if len(self.history) > config.AGENT_CONTEXT_WINDOW * 2:
                        self.history = self.history[-config.AGENT_CONTEXT_WINDOW * 2:]

                    return {
                        "response": final_text,
                        "tool_calls": tool_calls_made,
                        "actions": [],
                    }

                # Add model response and function results to contents
                contents.append(response.candidates[0].content)
                contents.append(types.Content(
                    role="user",
                    parts=function_responses,
                ))
            else:
                # Empty response
                return {
                    "response": "[Empty response from model]",
                    "tool_calls": tool_calls_made,
                    "actions": [],
                }

        # Max rounds exceeded
        return {
            "response": "[Max tool rounds reached]",
            "tool_calls": tool_calls_made,
            "actions": [],
        }

    def reset_conversation(self):
        """Reset the chat session (start fresh context)."""
        self.history = []
        logger.info("Conversation history reset")
