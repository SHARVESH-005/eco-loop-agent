"""
Eco-Loop Building Agent — Configuration
Central configuration for all system parameters.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Gemini API Keys (auto-fallback)
# ─────────────────────────────────────────────
GEMINI_API_KEYS = [
    "YOUR_GEMINI_API_KEY_HERES_1",
    "YOUR_GEMINI_API_KEY_HERES_2",
]
# Override with env var if present in .env file
if os.getenv("GEMINI_API_KEY"):
    GEMINI_API_KEYS.insert(0, os.getenv("GEMINI_API_KEY"))

GEMINI_MODEL = "gemini-2.0-flash"

# ─────────────────────────────────────────────
# Building Simulation Parameters
# ─────────────────────────────────────────────
BUILDING_NAME = "DOE Small Office - Chicago"
NUM_ZONES = 5  # 4 perimeter + 1 core
ZONE_NAMES = [
    "Zone_North_Perimeter",
    "Zone_East_Perimeter", 
    "Zone_South_Perimeter",
    "Zone_West_Perimeter",
    "Zone_Core",
]

# Simulation period (1 summer week for demo)
SIM_START_MONTH = 7
SIM_START_DAY = 15
SIM_END_MONTH = 7
SIM_END_DAY = 21
SIM_TIMESTEP_MINUTES = 60  # 1-hour timesteps
TOTAL_HOURS = (SIM_END_DAY - SIM_START_DAY + 1) * 24  # 168 hours

# ─────────────────────────────────────────────
# Comfort & Control Targets
# ─────────────────────────────────────────────
COMFORT_TEMP_MIN = 20.0  # °C
COMFORT_TEMP_MAX = 26.0  # °C
PMV_TARGET_MIN = -0.5
PMV_TARGET_MAX = 0.5

# Default HVAC setpoints (baseline)
BASELINE_HEATING_SETPOINT = 21.0  # °C
BASELINE_COOLING_SETPOINT = 24.0  # °C

# AI control bounds (safety limits)
MIN_HEATING_SETPOINT = 18.0  # °C
MAX_HEATING_SETPOINT = 23.0  # °C
MIN_COOLING_SETPOINT = 23.0  # °C
MAX_COOLING_SETPOINT = 28.0  # °C

# Occupancy schedule (hour: fraction)
OCCUPANCY_SCHEDULE = {
    0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0,
    6: 0.1, 7: 0.5, 8: 0.9, 9: 1.0, 10: 1.0, 11: 1.0,
    12: 0.8, 13: 1.0, 14: 1.0, 15: 1.0, 16: 0.9, 17: 0.5,
    18: 0.2, 19: 0.1, 20: 0.0, 21: 0.0, 22: 0.0, 23: 0.0,
}

# ─────────────────────────────────────────────
# Grid Carbon Intensity (gCO₂/kWh by hour)
# Simulated time-of-use carbon signal
# ─────────────────────────────────────────────
GRID_CARBON_INTENSITY = {
    0: 280, 1: 260, 2: 250, 3: 240, 4: 250, 5: 270,
    6: 350, 7: 420, 8: 480, 9: 500, 10: 490, 11: 470,
    12: 450, 13: 460, 14: 480, 15: 500, 16: 520, 17: 550,
    18: 530, 19: 480, 20: 420, 21: 380, 22: 340, 23: 300,
}

# ─────────────────────────────────────────────
# EnergyPlus Installation
# ─────────────────────────────────────────────
ENERGYPLUS_DIR = r"C:\EnergyPlusV26-1-0"
ENERGYPLUS_EXE = ENERGYPLUS_DIR + r"\energyplus.exe"
ENERGYPLUS_IDD = ENERGYPLUS_DIR + r"\Energy+.idd"

# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────
import pathlib
PROJECT_ROOT = pathlib.Path(__file__).parent
MODELS_DIR = PROJECT_ROOT / "models"
DATA_DIR = PROJECT_ROOT / "data"
DOCS_DIR = PROJECT_ROOT / "docs"

# Ensure directories exist
MODELS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
DOCS_DIR.mkdir(exist_ok=True)

BASELINE_IDF = MODELS_DIR / "baseline.idf"
WEATHER_FILE = MODELS_DIR / "weather.epw"
BASELINE_RESULTS_CSV = DATA_DIR / "baseline_results.csv"
OPTIMIZED_RESULTS_CSV = DATA_DIR / "optimized_results.csv"
AGENT_DECISIONS_LOG = DATA_DIR / "agent_decisions.log"

# ─────────────────────────────────────────────
# LLM Agent Settings
# ─────────────────────────────────────────────
AGENT_CONTEXT_WINDOW = 5  # Keep last N timesteps in context
STRATEGY_REVIEW_INTERVAL = 6  # Review strategy every N timesteps
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30  # seconds
