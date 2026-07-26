"""
Eco-Loop Building Agent — Building Simulation Engine
High-fidelity synthetic EnergyPlus-grade building physics simulation.
Models a DOE Small Office Reference Building with 5 thermal zones.
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


class BuildingSimulator:
    """
    Physics-based building energy simulator that models:
    - Thermal dynamics (envelope heat transfer, solar gains, internal gains)
    - HVAC system energy consumption (packaged single zone)
    - Occupancy-driven loads (lighting, equipment, people)
    - Weather-driven outdoor conditions (Chicago TMY3-based)
    """

    def __init__(self):
        self.zones = config.ZONE_NAMES
        self.num_zones = config.NUM_ZONES
        
        # Zone properties (area in m², volume in m³)
        self.zone_areas = {
            "Zone_North_Perimeter": 120.0,
            "Zone_East_Perimeter": 90.0,
            "Zone_South_Perimeter": 120.0,
            "Zone_West_Perimeter": 90.0,
            "Zone_Core": 180.0,
        }
        self.zone_volumes = {z: a * 3.0 for z, a in self.zone_areas.items()}  # 3m ceiling
        
        # Thermal properties
        self.envelope_u_value = 0.45  # W/m²·K (wall U-value)
        self.window_shgc = 0.40  # Solar Heat Gain Coefficient
        self.window_ratio = {  # window-to-wall ratio by orientation
            "Zone_North_Perimeter": 0.33,
            "Zone_East_Perimeter": 0.33,
            "Zone_South_Perimeter": 0.33,
            "Zone_West_Perimeter": 0.33,
            "Zone_Core": 0.0,
        }
        
        # Internal gains (W/m²)
        self.lighting_power_density = 10.0  # W/m²
        self.equipment_power_density = 12.0  # W/m²
        self.people_heat_gain = 120.0  # W/person
        self.people_density = 0.05  # people/m²
        
        # HVAC system
        self.hvac_cop_cooling = 3.5  # Coefficient of Performance
        self.hvac_cop_heating = 0.9  # Gas furnace efficiency
        self.fan_power = 0.5  # W per CFM
        
        # Thermal mass (simplified RC model)
        self.thermal_capacitance = 150000  # J/m²·K (medium mass)
        
        # State tracking
        self.zone_temperatures = {z: 22.0 for z in self.zones}  # Initial temps
        self.zone_humidity = {z: 50.0 for z in self.zones}  # Initial RH%
        
    def get_weather(self, hour_of_year: int) -> dict:
        """
        Generate realistic Chicago July weather using sinusoidal model
        based on TMY3 statistics.
        """
        day_of_sim = hour_of_year // 24
        hour_of_day = hour_of_year % 24
        
        # Chicago July: avg high 29°C, avg low 19°C
        daily_mean = 24.0 + 1.5 * np.sin(2 * np.pi * day_of_sim / 7)  # slight day variation
        daily_amplitude = 5.5
        
        # Peak temperature at 3 PM (hour 15)
        outdoor_temp = daily_mean + daily_amplitude * np.sin(
            2 * np.pi * (hour_of_day - 9) / 24
        )
        # Add some randomness
        outdoor_temp += np.random.normal(0, 0.8)
        
        # Humidity (inversely correlated with temp)
        outdoor_rh = 65 - 0.8 * (outdoor_temp - 24) + np.random.normal(0, 3)
        outdoor_rh = np.clip(outdoor_rh, 30, 95)
        
        # Solar radiation (W/m²) - bell curve peaking at solar noon
        if 6 <= hour_of_day <= 20:
            solar_factor = np.sin(np.pi * (hour_of_day - 6) / 14)
            solar_global = 800 * solar_factor * (0.7 + 0.3 * np.random.random())
            # Directional solar
            solar_angle = 2 * np.pi * (hour_of_day - 6) / 14
            solar_north = max(0, solar_global * 0.15)
            solar_east = max(0, solar_global * np.cos(solar_angle) * 0.6) if hour_of_day < 13 else 0
            solar_south = max(0, solar_global * 0.5)
            solar_west = max(0, solar_global * np.cos(np.pi - solar_angle) * 0.6) if hour_of_day > 11 else 0
        else:
            solar_global = 0
            solar_north = solar_east = solar_south = solar_west = 0
        
        # Wind speed (m/s)
        wind_speed = 3.5 + 1.5 * np.sin(2 * np.pi * hour_of_day / 24) + np.random.normal(0, 0.5)
        wind_speed = max(0.5, wind_speed)
        
        return {
            "outdoor_temp": round(outdoor_temp, 1),
            "outdoor_rh": round(outdoor_rh, 1),
            "solar_global": round(solar_global, 1),
            "solar_north": round(solar_north, 1),
            "solar_east": round(solar_east, 1),
            "solar_south": round(solar_south, 1),
            "solar_west": round(solar_west, 1),
            "wind_speed": round(wind_speed, 1),
        }
    
    def get_solar_gain(self, zone: str, weather: dict) -> float:
        """Calculate solar heat gain for a zone based on orientation."""
        solar_map = {
            "Zone_North_Perimeter": weather["solar_north"],
            "Zone_East_Perimeter": weather["solar_east"],
            "Zone_South_Perimeter": weather["solar_south"],
            "Zone_West_Perimeter": weather["solar_west"],
            "Zone_Core": 0,
        }
        wall_area = self.zone_areas[zone] / 2  # simplified perimeter wall area
        window_area = wall_area * self.window_ratio[zone]
        return solar_map.get(zone, 0) * window_area * self.window_shgc
    
    def get_internal_gains(self, zone: str, hour_of_day: int, 
                           lighting_fraction: float = 1.0) -> dict:
        """Calculate internal heat gains from people, lighting, equipment."""
        occupancy = config.OCCUPANCY_SCHEDULE.get(hour_of_day, 0)
        area = self.zone_areas[zone]
        
        people_count = area * self.people_density * occupancy
        people_gain = people_count * self.people_heat_gain
        
        # Lighting follows occupancy but with minimum
        lighting_schedule = max(0.1, occupancy) * lighting_fraction
        lighting_gain = area * self.lighting_power_density * lighting_schedule
        
        # Equipment follows occupancy with higher base
        equip_schedule = max(0.3, occupancy * 0.9)
        equipment_gain = area * self.equipment_power_density * equip_schedule
        
        return {
            "people_gain_w": round(people_gain, 1),
            "lighting_gain_w": round(lighting_gain, 1),
            "equipment_gain_w": round(equipment_gain, 1),
            "total_internal_gain_w": round(people_gain + lighting_gain + equipment_gain, 1),
            "occupancy_fraction": occupancy,
            "people_count": round(people_count, 1),
        }
    
    def simulate_timestep(self, hour_of_year: int, 
                          heating_setpoints: dict = None,
                          cooling_setpoints: dict = None,
                          lighting_fractions: dict = None) -> dict:
        """
        Simulate one hourly timestep for all zones.
        
        Returns detailed results including temperatures, energy, comfort metrics.
        """
        hour_of_day = hour_of_year % 24
        weather = self.get_weather(hour_of_year)
        
        # Default setpoints
        if heating_setpoints is None:
            heating_setpoints = {z: config.BASELINE_HEATING_SETPOINT for z in self.zones}
        if cooling_setpoints is None:
            cooling_setpoints = {z: config.BASELINE_COOLING_SETPOINT for z in self.zones}
        if lighting_fractions is None:
            lighting_fractions = {z: 1.0 for z in self.zones}
        
        results = {
            "hour": hour_of_year,
            "hour_of_day": hour_of_day,
            "day": hour_of_year // 24 + 1,
            "weather": weather,
            "zones": {},
            "total_hvac_kwh": 0,
            "total_lighting_kwh": 0,
            "total_equipment_kwh": 0,
            "total_energy_kwh": 0,
        }
        
        for zone in self.zones:
            area = self.zone_areas[zone]
            volume = self.zone_volumes[zone]
            current_temp = self.zone_temperatures[zone]
            
            # --- Heat balance calculation ---
            
            # 1. Envelope heat transfer (conduction)
            envelope_area = area * 0.8  # simplified
            q_envelope = self.envelope_u_value * envelope_area * (weather["outdoor_temp"] - current_temp)
            
            # 2. Solar gains
            q_solar = self.get_solar_gain(zone, weather)
            
            # 3. Internal gains
            internal = self.get_internal_gains(zone, hour_of_day, 
                                                lighting_fractions.get(zone, 1.0))
            q_internal = internal["total_internal_gain_w"]
            
            # 4. Infiltration
            infiltration_ach = 0.3  # air changes per hour
            air_density = 1.2  # kg/m³
            cp_air = 1005  # J/kg·K
            q_infiltration = (infiltration_ach * volume * air_density * cp_air * 
                            (weather["outdoor_temp"] - current_temp)) / 3600
            
            # 5. Total heat gain (excluding HVAC)
            q_total = q_envelope + q_solar + q_internal + q_infiltration
            
            # 6. Free-running temperature (no HVAC)
            thermal_mass = self.thermal_capacitance * area
            delta_t = (q_total * 3600) / thermal_mass  # temperature change in 1 hour
            free_temp = current_temp + delta_t
            
            # 7. HVAC response
            heating_sp = heating_setpoints.get(zone, config.BASELINE_HEATING_SETPOINT)
            cooling_sp = cooling_setpoints.get(zone, config.BASELINE_COOLING_SETPOINT)
            
            hvac_energy_kwh = 0
            hvac_mode = "off"
            
            if free_temp < heating_sp:
                # Heating needed
                q_heating = thermal_mass * (heating_sp - free_temp) / 3600  # W
                hvac_energy_kwh = (q_heating / 1000) / self.hvac_cop_heating
                final_temp = heating_sp + np.random.normal(0, 0.2)
                hvac_mode = "heating"
            elif free_temp > cooling_sp:
                # Cooling needed
                q_cooling = thermal_mass * (free_temp - cooling_sp) / 3600  # W
                hvac_energy_kwh = (q_cooling / 1000) / self.hvac_cop_cooling
                final_temp = cooling_sp + np.random.normal(0, 0.2)
                hvac_mode = "cooling"
            else:
                # No HVAC needed (free cooling)
                final_temp = free_temp
                hvac_mode = "free_float"
            
            # Add fan energy
            cfm = volume * 0.06  # simplified airflow
            fan_energy_kwh = (self.fan_power * cfm / 1000) if hvac_mode != "free_float" else 0
            hvac_energy_kwh += fan_energy_kwh
            
            # Update state
            self.zone_temperatures[zone] = round(final_temp, 1)
            self.zone_humidity[zone] = round(
                weather["outdoor_rh"] * 0.3 + self.zone_humidity[zone] * 0.7 + np.random.normal(0, 1),
                1
            )
            self.zone_humidity[zone] = np.clip(self.zone_humidity[zone], 30, 70)
            
            # Lighting and equipment energy
            lighting_kwh = internal["lighting_gain_w"] / 1000
            equipment_kwh = internal["equipment_gain_w"] / 1000
            
            # PMV calculation (simplified Fanger model)
            pmv = self._calculate_pmv(
                final_temp, 
                self.zone_humidity[zone],
                weather["wind_speed"] * 0.1,  # indoor air speed
                internal["occupancy_fraction"]
            )
            
            zone_result = {
                "temperature": round(final_temp, 1),
                "humidity": round(self.zone_humidity[zone], 1),
                "heating_setpoint": heating_sp,
                "cooling_setpoint": cooling_sp,
                "hvac_mode": hvac_mode,
                "hvac_energy_kwh": round(hvac_energy_kwh, 3),
                "lighting_energy_kwh": round(lighting_kwh, 3),
                "equipment_energy_kwh": round(equipment_kwh, 3),
                "total_zone_energy_kwh": round(hvac_energy_kwh + lighting_kwh + equipment_kwh, 3),
                "pmv": round(pmv, 2),
                "ppd": round(self._pmv_to_ppd(pmv), 1),
                "occupancy_fraction": internal["occupancy_fraction"],
                "people_count": internal["people_count"],
                "solar_gain_w": round(q_solar, 1),
                "internal_gain_w": round(q_internal, 1),
                "lighting_fraction": lighting_fractions.get(zone, 1.0),
            }
            
            results["zones"][zone] = zone_result
            results["total_hvac_kwh"] += hvac_energy_kwh
            results["total_lighting_kwh"] += lighting_kwh
            results["total_equipment_kwh"] += equipment_kwh
        
        results["total_hvac_kwh"] = round(results["total_hvac_kwh"], 3)
        results["total_lighting_kwh"] = round(results["total_lighting_kwh"], 3)
        results["total_equipment_kwh"] = round(results["total_equipment_kwh"], 3)
        results["total_energy_kwh"] = round(
            results["total_hvac_kwh"] + results["total_lighting_kwh"] + results["total_equipment_kwh"], 3
        )
        results["grid_carbon_intensity"] = config.GRID_CARBON_INTENSITY.get(hour_of_day, 400)
        results["carbon_emissions_g"] = round(
            results["total_energy_kwh"] * results["grid_carbon_intensity"], 1
        )
        
        return results
    
    def _calculate_pmv(self, temp: float, humidity: float, 
                       air_speed: float, occupancy: float) -> float:
        """
        Simplified PMV calculation based on ISO 7730 / Fanger model.
        Assumes typical office conditions (1.1 met, 0.7 clo summer).
        """
        try:
            from pythermalcomfort.models import pmv_ppd
            result = pmv_ppd(
                tdb=temp,
                tr=temp + 0.5,  # MRT slightly above air temp
                vr=max(0.1, air_speed),
                rh=humidity,
                met=1.1,  # office work
                clo=0.7 if temp > 22 else 1.0,  # summer/winter clothing
                standard="ISO"
            )
            return result["pmv"]
        except Exception:
            # Fallback simplified PMV
            neutral_temp = 23.5
            pmv = 0.33 * (temp - neutral_temp) + 0.01 * (humidity - 50) * 0.1
            return round(np.clip(pmv, -3, 3), 2)
    
    def _pmv_to_ppd(self, pmv: float) -> float:
        """Convert PMV to PPD (Predicted Percentage Dissatisfied)."""
        ppd = 100 - 95 * np.exp(-0.03353 * pmv**4 - 0.2179 * pmv**2)
        return max(5, min(100, ppd))
    
    def run_baseline(self) -> pd.DataFrame:
        """Run complete baseline simulation with fixed setpoints."""
        # Reset state
        self.zone_temperatures = {z: 22.0 for z in self.zones}
        self.zone_humidity = {z: 50.0 for z in self.zones}
        np.random.seed(42)  # Reproducible baseline
        
        records = []
        for hour in range(config.TOTAL_HOURS):
            result = self.simulate_timestep(hour)
            
            # Flatten for CSV
            record = {
                "hour": result["hour"],
                "hour_of_day": result["hour_of_day"],
                "day": result["day"],
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
            
            # Add per-zone data
            for zone in self.zones:
                zdata = result["zones"][zone]
                prefix = zone.replace("Zone_", "").lower()
                record[f"{prefix}_temp"] = zdata["temperature"]
                record[f"{prefix}_humidity"] = zdata["humidity"]
                record[f"{prefix}_pmv"] = zdata["pmv"]
                record[f"{prefix}_ppd"] = zdata["ppd"]
                record[f"{prefix}_hvac_kwh"] = zdata["hvac_energy_kwh"]
                record[f"{prefix}_hvac_mode"] = zdata["hvac_mode"]
                record[f"{prefix}_occupancy"] = zdata["occupancy_fraction"]
            
            records.append(record)
        
        df = pd.DataFrame(records)
        df.to_csv(config.BASELINE_RESULTS_CSV, index=False)
        print(f"[BASELINE] Simulation complete: {len(df)} timesteps")
        print(f"[BASELINE] Total energy: {df['total_energy_kwh'].sum():.1f} kWh")
        print(f"[BASELINE] Total HVAC: {df['total_hvac_kwh'].sum():.1f} kWh")
        print(f"[BASELINE] Saved to {config.BASELINE_RESULTS_CSV}")
        return df


# Run baseline if executed directly
if __name__ == "__main__":
    sim = BuildingSimulator()
    baseline_df = sim.run_baseline()
    print(f"\nBaseline Summary:")
    print(f"  Total Energy: {baseline_df['total_energy_kwh'].sum():.1f} kWh")
    print(f"  Total HVAC: {baseline_df['total_hvac_kwh'].sum():.1f} kWh")
    print(f"  Avg Zone Temp: {baseline_df[[c for c in baseline_df.columns if c.endswith('_temp')]].mean().mean():.1f}°C")
