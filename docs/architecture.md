# Eco-Loop Building Agent — System Architecture

## 1. System Overview

Eco-Loop is an autonomous building energy optimization system that creates a closed-loop control pipeline between a physics-based building simulator and an AI cognitive engine.

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLOSED-LOOP CONTROL PIPELINE                 │
│                                                                 │
│   ┌──────────────┐   FEEDBACK    ┌──────────────────────────┐   │
│   │  BUILDING    │──────────────▶│  MCP SERVER              │   │
│   │  SIMULATOR   │  (sensor      │  (Tool Interface Layer)  │   │
│   │  (EnergyPlus │   data)       │                          │   │
│   │   Physics)   │               │  ┌────────────────────┐  │   │
│   │              │   CONTROL     │  │ read_building_     │  │   │
│   │  • 5 Zones   │◀─────────────│  │   sensors()        │  │   │
│   │  • HVAC      │  (setpoints,  │  │ calculate_pmv()    │  │   │
│   │  • Lighting  │   schedules)  │  │ set_thermostat_    │  │   │
│   │  • Weather   │               │  │   setpoint()       │  │   │
│   └──────────────┘               │  │ adjust_lighting_   │  │   │
│                                  │  │   schedule()       │  │   │
│                                  │  │ analyze_energy_    │  │   │
│                                  │  │   pattern()        │  │   │
│                                  │  │ get_grid_carbon_   │  │   │
│                                  │  │   intensity()      │  │   │
│                                  │  │ get_weather_       │  │   │
│                                  │  │   forecast()       │  │   │
│                                  │  │ log_decision()     │  │   │
│                                  │  └────────┬───────────┘  │   │
│                                  └───────────┼──────────────┘   │
│                                              │                  │
│                                    ┌─────────▼─────────┐        │
│                                    │  GEMINI 2.0 FLASH │        │
│                                    │  (Cognitive Engine)│       │
│                                    │                    │       │
│                                    │  • Tool Calling    │       │
│                                    │  • Reasoning       │       │
│                                    │  • Decision Making │       │
│                                    └────────────────────┘       │
│                                                                 │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │              DATA LOGGER → DASHBOARD                     │  │
│   │  baseline_results.csv | optimized_results.csv            │  │
│   │  agent_decisions.log  | Plotly Dash Visualization        │  │
│   └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 2. Tool-Calling Architecture

### 2.1 Protocol Design
We implement the **Model Context Protocol (MCP)** pattern where:
- The **MCP Server** exposes 8 typed tools as function declarations
- **Google Gemini 2.0 Flash** acts as the cognitive engine with native function calling
- The **Orchestrator** manages the simulation-reasoning-control loop

### 2.2 Tool Execution Flow
```
1. Orchestrator runs simulation timestep
2. Sensor data pushed to MCP Server shared state
3. Orchestrator sends timestep prompt to Gemini
4. Gemini analyzes prompt → decides to call tools
5. Gemini returns function_call(s)
6. Orchestrator executes tools via MCP Server
7. Results sent back to Gemini
8. Gemini provides final analysis + control decisions
9. Orchestrator extracts pending actions from MCP Server
10. Actions applied to next simulation timestep
```

### 2.3 Tool Definitions

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `read_building_sensors` | Get real-time zone data | zone_name | JSON: temps, humidity, PMV, energy |
| `calculate_pmv` | Thermal comfort index | temp, humidity, air_speed | PMV value + comfort status |
| `analyze_energy_pattern` | Trend detection | n_steps | Trend direction + insights |
| `set_thermostat_setpoint` | HVAC control | zone, heating_sp, cooling_sp | Confirmation |
| `adjust_lighting_schedule` | Lighting control | zone, fraction | Confirmation + savings |
| `get_grid_carbon_intensity` | Carbon signal | hour | gCO₂/kWh + recommendation |
| `get_weather_forecast` | Predictive control | hours_ahead | Forecast array |
| `log_decision` | Audit trail | reasoning, action, impact | Log confirmation |

## 3. Prompt Engineering Strategy

### 3.1 System Prompt
The system prompt establishes the agent's identity, mission, available tools, control strategy guidelines, and safety constraints. Key elements:
- **Role**: Autonomous Building Energy Optimization Agent
- **Objective**: Minimize energy while maintaining PMV [-0.5, +0.5]
- **Strategy**: Occupation-aware setbacks, pre-cooling, daylight harvesting, peak demand response
- **Constraints**: Temperature bounds, deadband requirements, comfort limits

### 3.2 Per-Timestep Prompt
Each hour, the agent receives a structured data injection containing:
- Current weather conditions (outdoor temp, humidity, solar radiation)
- Zone status table (temperature, humidity, PMV, HVAC mode, occupancy)
- Energy metrics (HVAC, lighting, equipment, total kWh)
- Cumulative savings vs baseline
- Carbon intensity signal

### 3.3 Strategy Review Prompt
Every 6 timesteps, a higher-level review prompt asks the agent to:
- Evaluate overall performance trends
- Identify zones with persistent comfort issues
- Consider predictive strategies (pre-cooling, load shifting)
- Adjust the global strategy if needed

## 4. Prompt Latency Management

| Strategy | Implementation |
|----------|---------------|
| **Batch Processing** | LLM called every hour (not every minute), amortizing inference cost |
| **Compact Data Format** | Structured JSON with abbreviated zone names, rounded values |
| **Sliding Window** | Only last 12 hours kept in context; older data summarized |
| **Session Reset** | Chat history reset every 24 simulated hours to prevent context overflow |
| **Key Rotation** | 3 API keys with automatic failover on rate limits |
| **Reduced Calls** | Unoccupied hours: LLM called every 3 hours instead of every hour |

## 5. Handling Lengthy Simulation Logs

| Challenge | Solution |
|-----------|----------|
| Large CSV outputs | Only parse relevant columns (temps, energy, PMV) |
| Growing history | Sliding window: keep last 12 timesteps, drop older |
| Decision log growth | Separate log file (JSONL), only recent 5 shown to LLM |
| Trend analysis | `analyze_energy_pattern` tool summarizes N timesteps into one insight |
| Multi-zone data | Compact tabular format in prompt, not raw JSON dump |

## 6. Building Simulation Model

### 6.1 DOE Small Office Reference Building
- **Location**: Chicago, IL (ASHRAE Climate Zone 5A)
- **5 Thermal Zones**: North/East/South/West Perimeter + Core
- **HVAC**: Packaged Single Zone (PSZ) with gas heating, DX cooling
- **Baseline**: ASHRAE 90.1-2019 compliant schedules

### 6.2 Physics Model
- RC thermal network (resistance-capacitance) for zone heat balance
- Solar heat gain with orientation-dependent SHGC
- Internal gains: occupancy-scheduled people, lighting, equipment loads
- HVAC energy: COP-based cooling (3.5), gas heating efficiency (0.9)
- Infiltration: 0.3 ACH

## 7. Energy Conservation Measures (ECMs)

The AI agent applies these ECMs dynamically:

1. **Thermostat Setback** — Widen deadband during unoccupied hours
2. **Optimal Start/Stop** — Pre-condition building before occupancy
3. **Daylight Harvesting** — Reduce lighting in perimeter zones with high solar
4. **Demand Response** — Reduce loads during high carbon intensity periods
5. **Zone-Specific Tuning** — Different setpoints per zone based on orientation/load
6. **Thermal Mass Exploitation** — Pre-cool during cheap/clean energy periods

## 8. Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Python | 3.10+ |
| Simulation | Custom physics engine (EnergyPlus-grade) | - |
| LLM | Google Gemini 2.0 Flash | via API |
| Protocol | Model Context Protocol (MCP) | 1.0 |
| Dashboard | Plotly Dash + Bootstrap | 2.14+ |
| Comfort | pythermalcomfort (ISO 7730 PMV/PPD) | 2.8+ |
| Data | pandas, numpy | - |
