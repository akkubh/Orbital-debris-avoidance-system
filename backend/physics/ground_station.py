"""
Ground station Line-of-Sight (LOS) checker.
Determines if a satellite can receive a command from at least one ground station.
All angles in degrees, distances in km.
"""
import math
from physics.propagator import eci_to_geodetic, compute_gmst

RE = 6378.137  # km

# Ground station database (from problem spec)
GROUND_STATIONS = [
    {"id": "GS-001", "name": "ISTRAC_Bengaluru",       "lat":  13.0333, "lon":  77.5167, "elev_m":  820, "min_el": 5.0},
    {"id": "GS-002", "name": "Svalbard_Sat_Station",   "lat":  78.2297, "lon":  15.4077, "elev_m":  400, "min_el": 5.0},
    {"id": "GS-003", "name": "Goldstone_Tracking",     "lat":  35.4266, "lon": -116.8900,"elev_m": 1000, "min_el":10.0},
    {"id": "GS-004", "name": "Punta_Arenas",           "lat": -53.1500, "lon": -70.9167, "elev_m":   30, "min_el": 5.0},
    {"id": "GS-005", "name": "IIT_Delhi_Ground_Node",  "lat":  28.5450, "lon":  77.1926, "elev_m":  225, "min_el":15.0},
    {"id": "GS-006", "name": "McMurdo_Station",        "lat": -77.8463, "lon": 166.6682, "elev_m":   10, "min_el": 5.0},
]


def elevation_angle(gs_lat_deg: float, gs_lon_deg: float,
                    sat_lat_deg: float, sat_lon_deg: float, sat_alt_km: float) -> float:
    """
    Compute elevation angle (degrees) of satellite as seen from ground station.
    Uses spherical Earth model.
    """
    # Convert to radians
    gs_lat  = math.radians(gs_lat_deg)
    gs_lon  = math.radians(gs_lon_deg)
    sat_lat = math.radians(sat_lat_deg)
    sat_lon = math.radians(sat_lon_deg)

    # Central angle between GS and satellite sub-point
    cos_central = (math.sin(gs_lat) * math.sin(sat_lat)
                   + math.cos(gs_lat) * math.cos(sat_lat) * math.cos(sat_lon - gs_lon))
    cos_central = max(-1.0, min(1.0, cos_central))
    central_angle = math.acos(cos_central)

    # Elevation angle
    rho = RE / (RE + sat_alt_km)   # ratio
    sin_el = math.cos(central_angle) - rho
    cos_el = math.sin(central_angle)
    if cos_el < 1e-10:
        return 90.0 if sin_el > 0 else -90.0

    el_rad = math.atan2(sin_el, cos_el)
    return math.degrees(el_rad)


def has_line_of_sight(sat_r_eci: list, sim_time_iso: str) -> tuple[bool, list[str]]:
    """
    Check if satellite has LOS to at least one ground station.
    Returns (has_los: bool, visible_station_ids: list[str])
    """
    gmst = compute_gmst(sim_time_iso)
    sat_lat, sat_lon, sat_alt = eci_to_geodetic(sat_r_eci, gmst)

    visible = []
    for gs in GROUND_STATIONS:
        el = elevation_angle(gs["lat"], gs["lon"], sat_lat, sat_lon, sat_alt)
        if el >= gs["min_el"]:
            visible.append(gs["id"])

    return len(visible) > 0, visible


def next_los_window(sat_r: list, sat_v: list, sim_time_iso: str,
                    search_window_s: float = 7200.0, dt: float = 30.0) -> dict:
    """
    Find the next LOS window for a satellite currently in blackout.
    Propagates forward up to search_window_s seconds.
    Returns {"found": bool, "offset_s": float, "station_ids": list}
    """
    from physics.propagator import rk4_step
    import numpy as np
    from datetime import datetime, timezone, timedelta

    state = np.array(sat_r + sat_v, dtype=float)
    dt_obj = datetime.fromisoformat(sim_time_iso.replace("Z", "+00:00"))
    elapsed = 0.0

    while elapsed < search_window_s:
        state   = rk4_step(state, dt)
        elapsed += dt

        future_time = (dt_obj + timedelta(seconds=elapsed)).isoformat()
        found, stations = has_line_of_sight(state[:3].tolist(), future_time)
        if found:
            return {"found": True, "offset_s": elapsed, "station_ids": stations}

    return {"found": False, "offset_s": None, "station_ids": []}
