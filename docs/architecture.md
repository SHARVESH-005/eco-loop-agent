# Eco-Loop Building Agent — System Architecture

---

## Overview

Eco-Loop is an autonomous building energy optimization system that implements a closed-loop control pipeline between a physics-based building simulator and an AI cognitive engine. The agent continuously senses the simulated building, reasons about energy and comfort trade-offs, and issues control actions to minimize energy consumption while maintaining occupant thermal comfort.

Key goals:
- Maintain Predicted Mean Vote (PMV) within [-0.5, +0.5]
- Minimize building energy consumption and peak demand
- Use predictive strategies (pre-cooling, load shifting) and carbon-aware control

---

## 1 System Architecture (High-level)

The system is composed of three main components:

- Simulator: physics-based building model (5 thermal zones, HVAC, lighting, weather)
- MCP Server: typed tool interface that exposes building and utility functions
- Cognitive Engine: Google Gemini 2.0 Flash (LLM) that reasons and calls tools
- Orchestrator: coordinates simulation timesteps and the tool-calling loop
- Dashboard / Data Logger: stores results and visualizes performance


Simple diagram (conceptual):

```
+-------------------+       +----------------+       +--------------------+
|   BUILDING        |  -->  |   MCP SERVER   |  <--> |  COGNITIVE ENGINE  |
|   SIMULATOR       |       | (tool layer)   |       | (Gemini 2.0 Flash) |
| (physics engine)  |  <--  |                |       |  • tool calling    |
+-------------------+       +----------------+       +--------------------+
         |   ^                     |   ^                    |
         |   |   control/actions   |   |   function calls    |
         v   |                     v   |                    v
+-------------------+       +----------------+       +--------------------+
|   DATA LOGGER     |       |  ORCHESTRATOR  |       |   DASHBOARD (UI)   |
| (CSV / JSONL / DB)|       | (loop manager) |       |  (Plotly Dash)     |
+-------------------+       +----------------+       +--------------------+
```

---

## 2 Tool-Calling Architecture

### 2.1 Model Context Protocol (MCP)

We follow the Model Context Protocol (MCP) pattern:
- MCP Server exposes typed function-like tools (stable signatures, typed inputs/outputs)
- Gemini uses native function/tool-calling to request sensor reads, analytics, and actuations
- The Orchestrator mediates: runs simulation timesteps, pushes sensor state, invokes Gemini, executes actions


### 2.2 Execution Flow

1. Orchestrator steps the simulator by one timestep
2. Sensor data is pushed to MCP Server shared state
3. Orchestrator sends a structured timestep prompt to Gemini
4. Gemini analyzes inputs and may return function_call(s)
5. Orchestrator executes requested tools via the MCP Server
6. Results are returned to Gemini; Gemini returns final analysis + control recommendations
7. Orchestrator extracts pending actions and applies them to the next timestep


### 2.3 Tool Definitions

| Tool name | Purpose | Inputs | Output |
|-----------|---------|--------|--------|
| `read_building_sensors` | Read real-time zone telemetry | zone_name | JSON `{ temps, humidity, pmv, energy }` |
| `calculate_pmv` | Calculate thermal comfort index | `{temp, humidity, air_speed}` | PMV value + comfort status |
| `analyze_energy_pattern` | Summarize recent trends | `n_steps` | Trend direction + insights |
| `set_thermostat_setpoint` | Issue HVAC setpoint changes | `{zone, heating_sp, cooling_sp}` | Confirmation |
| `adjust_lighting_schedule` | Update lighting fractions/schedules | `{zone, fraction}` | Confirmation + estimated savings |
| `get_grid_carbon_intensity` | Fetch carbon signal | `hour` | `gCO2/kWh` + recommendation |
| `get_weather_forecast` | Forecasted weather | `hours_ahead` | Forecast array |
| `log_decision` | Append audit trail entry | `{reasoning, action, impact}` | Log confirmation |

Notes:
- Tool inputs/outputs use compact JSON-friendly structures to keep prompts concise.
- All tool calls are logged to the Decision Log (JSONL) for offline analysis and auditing.

---

## 3 Prompting Strategy

### System prompt (agent identity & constraints)
The system prompt defines the agent mission, available tools, safety constraints, and control objectives. Highlights:
- Role: Autonomous Building Energy Optimization Agent
- Objective: Minimize energy while keeping PMV within [-0.5, +0.5]
- Strategy primitives: occupancy-aware setbacks, pre-cooling, daylight harvesting, peak demand reduction
- Safety constraints: temperature bounds, deadband rules, override on critical alarms


### Per-timestep prompt (hourly)
Each timestep prompt contains a compact structured payload:
- Current weather (outdoor temp, humidity, solar radiation)
- Zone status table: `zone, temp, humidity, pmv, hvac_mode, occupancy`
- Energy metrics: HVAC, lighting, equipment, total (kWh)
- Cumulative savings vs baseline
- Carbon intensity signal

To reduce latency and token usage we:
- Call the LLM hourly (batching strategy)
- Round numeric values and use abbreviated zone names
- Keep a 12-hour sliding window and summarize older history


### Strategy review (every 6 timesteps)
A higher-level checkpoint where the agent:
- Reviews performance trends
- Flags zones with persistent comfort deviations
- Suggests strategy-level adjustments (e.g., tighten setbacks, change pre-cool windows)

---

## 4 Latency & Context Management

| Strategy | Implementation |
|---|---|
| Batch processing | Call LLM every hour; reduce frequency during unoccupied periods |
| Compact data format | Structured JSON + rounded values to save tokens |
| Sliding window | Keep last 12 timesteps in full detail; summarize older data |
| Session reset | Reset chat context every 24 simulated hours to avoid context bloat |
| Key rotation | Cycle through 3 API keys for rate-limit resilience |
| Reduced calls when unoccupied | LLM every 3 hours when building is empty |

---

## 5 Handling Large Logs & Outputs

Challenges and solutions:
- Large CSVs: only parse and keep relevant columns (temps, energy, PMV)
- Growing history: sliding window + periodic summaries
- Decision log growth: secondary JSONL file with recent N entries exposed to the LLM
- Trend analysis: use `analyze_energy_pattern` to condense N timesteps into one insight
- Multi-zone verbosity: present compact tabular summaries rather than raw dumps

---

## 6 Building Simulation Model

### Reference building
- DOE Small Office (Chicago, IL — ASHRAE Climate Zone 5A)
- 5 thermal zones: North / East / South / West perimeter + Core
- HVAC: Packaged single-zone (PSZ) with gas heating and DX cooling
- Baseline schedules: ASHRAE 90.1-2019


### Physics model
- RC thermal network per zone (resistance-capacitance heat balance)
- Orientation-dependent solar gains (SHGC)
- Internal gains: scheduled occupants, lighting, equipment
- HVAC efficiencies: cooling COP ≈ 3.5, gas heating eff ≈ 0.9
- Infiltration: 0.3 ACH

---

## 7 Energy Conservation Measures (ECMs)

The agent dynamically applies these ECMs:
1. Thermostat setback during unoccupied hours
2. Optimal start/stop (pre-conditioning before occupancy)
3. Daylight harvesting (reduce perimeter lighting when solar gains suffice)
4. Demand response (reduce loads during high-carbon or high-price periods)
5. Zone-specific tuning (per-orientation setpoints)
6. Thermal mass exploitation (pre-cool when energy is cheap/clean)

---

## 8 Technology Stack

| Component | Technology | Notes |
|---|---:|---|
| Language | Python | 3.10+
| Simulation | Custom physics engine | EnergyPlus-grade model
| LLM | Google Gemini 2.0 Flash | via API, native function calling
| Protocol | Model Context Protocol (MCP) | Typed tool interfaces
| Dashboard | Plotly Dash + Bootstrap | Interactive visualizations
| Comfort | pythermalcomfort | ISO 7730 PMV/PPD calculations
| Data | pandas, numpy | core data processing

---

## 9 Observability & Logs

- Decision Log: JSONL capturing {timestamp, reasoning, tool_calls, actions, expected_impact}
- Results CSVs: `baseline_results.csv`, `optimized_results.csv`
- Dashboard: real-time and historical visualizations (Plotly Dash)

---

## 10 Next Steps / Recommendations

- Add a sequence diagram (PlantUML or Mermaid) for the MCP call flow in docs/assets or README
- Create unit and integration tests around MCP tool contracts
- Add a reproducible example notebook that runs a short simulation and shows agent decisions

---

If you want, I can also:
- Add a Mermaid sequence diagram and embed it in the doc
- Generate a PlantUML image and add it to docs/assets
- Create a short example notebook that demonstrates a single control loop
