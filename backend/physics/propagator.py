"""
physics/propagator.py
─────────────────────
RK4 orbital propagator with J2 perturbation.
All units: km, km/s, seconds.

FIXES APPLIED:
  - Duplicate sgp4 and datetime imports removed
  - jday() now passes fractional seconds (now.second + now.microsecond/1e6)
    to avoid up to 7.5 km position error from integer-second truncation
"""

import math
import numpy as np
from datetime import datetime, timezone
from sgp4.api import Satrec, jday

# ── Constants ─────────────────────────────────────────────────────────────────
MU = 398600.4418   # km³/s²
RE = 6378.137      # km
J2 = 1.08263e-3


# ── J2 perturbation ───────────────────────────────────────────────────────────

def _j2_accel(r: np.ndarray) -> np.ndarray:
    x, y, z   = r
    r_norm    = np.linalg.norm(r)
    factor    = (3 / 2) * J2 * MU * RE**2 / r_norm**5
    common_xy = 5 * z**2 / r_norm**2 - 1
    ax = factor * x * common_xy
    ay = factor * y * common_xy
    az = factor * z * (5 * z**2 / r_norm**2 - 3)
    return np.array([ax, ay, az])


def _derivatives(state: np.ndarray) -> np.ndarray:
    r      = state[:3]
    v      = state[3:]
    r_norm = np.linalg.norm(r)
    a_grav = -MU / r_norm**3 * r
    a_j2   = _j2_accel(r)
    return np.concatenate([v, a_grav + a_j2])


# ── RK4 integrator ────────────────────────────────────────────────────────────

def rk4_step(state: np.ndarray, dt: float) -> np.ndarray:
    """Single RK4 step. state=[x,y,z,vx,vy,vz], dt in seconds."""
    k1 = _derivatives(state)
    k2 = _derivatives(state + 0.5 * dt * k1)
    k3 = _derivatives(state + 0.5 * dt * k2)
    k4 = _derivatives(state + dt * k3)
    return state + (dt / 6.0) * (k1 + 2*k2 + 2*k3 + k4)


def propagate(r: list, v: list, duration_s: float, dt: float = 10.0) -> tuple[list, list]:
    state   = np.array(r + v, dtype=float)
    elapsed = 0.0
    while elapsed < duration_s:
        step    = min(dt, duration_s - elapsed)
        state   = rk4_step(state, step)
        elapsed += step
    return state[:3].tolist(), state[3:].tolist()


def propagate_with_history(
    r: list, v: list, duration_s: float,
    record_interval: float = 60.0, dt: float = 10.0,
) -> list[dict]:
    state    = np.array(r + v, dtype=float)
    elapsed  = 0.0
    history  = [{"t": 0.0, "r": r[:], "v": v[:]}]
    next_rec = record_interval

    while elapsed < duration_s:
        step    = min(dt, duration_s - elapsed)
        state   = rk4_step(state, step)
        elapsed += step
        if elapsed >= next_rec:
            history.append({
                "t": elapsed,
                "r": state[:3].tolist(),
                "v": state[3:].tolist(),
            })
            next_rec += record_interval

    return history


def apply_delta_v(v: list, delta_v_eci: list) -> list:
    return (np.array(v) + np.array(delta_v_eci)).tolist()


# ── Coordinate conversions ────────────────────────────────────────────────────

def eci_to_geodetic(r_eci: list, gmst_rad: float) -> tuple[float, float, float]:
    x, y, z  = r_eci
    ecef_x   =  x * np.cos(gmst_rad) + y * np.sin(gmst_rad)
    ecef_y   = -x * np.sin(gmst_rad) + y * np.cos(gmst_rad)
    ecef_z   =  z
    lon_rad  = np.arctan2(ecef_y, ecef_x)
    p        = np.sqrt(ecef_x**2 + ecef_y**2)
    lat_rad  = np.arctan2(ecef_z, p)
    alt_km   = np.sqrt(ecef_x**2 + ecef_y**2 + ecef_z**2) - RE
    return float(np.degrees(lat_rad)), float(np.degrees(lon_rad)), float(alt_km)


def compute_gmst(sim_time_iso: str) -> float:
    dt     = datetime.fromisoformat(sim_time_iso.replace("Z", "+00:00"))
    jd     = (dt - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0 + 2451545.0
    T      = (jd - 2451545.0) / 36525.0
    g_deg  = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
              + 0.000387933 * T**2) % 360.0
    return math.radians(g_deg)


# ── TLE → state vector ────────────────────────────────────────────────────────

def tle_to_state_vector(line1: str, line2: str) -> dict | None:
    """
    FIX: pass fractional seconds to jday() to avoid up to 7.5 km position error
    from integer-second truncation at LEO orbital velocity (~7.5 km/s).
    """
    sat = Satrec.twoline2rv(line1, line2)
    now = datetime.now(timezone.utc)
    # FIX: include microseconds so position accuracy is < 1 m instead of < 7500 m
    sec_frac = now.second + now.microsecond / 1_000_000.0
    jd, fr   = jday(now.year, now.month, now.day, now.hour, now.minute, sec_frac)
    e, r, v  = sat.sgp4(jd, fr)
    if e != 0:
        return None
    return {"position": list(r), "velocity": list(v)}