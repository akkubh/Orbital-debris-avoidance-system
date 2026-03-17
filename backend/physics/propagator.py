"""
RK4 orbital propagator with J2 perturbation.
All units: km, km/s, seconds.
"""
import numpy as np

# ── Constants ─────────────────────────────────────────────────────────
MU = 398600.4418     # km³/s²
RE = 6378.137        # km
J2 = 1.08263e-3


def _j2_accel(r: np.ndarray) -> np.ndarray:
    """J2 perturbation acceleration vector in ECI (km/s²)."""
    x, y, z = r
    r_norm = np.linalg.norm(r)
    factor = (3 / 2) * J2 * MU * RE ** 2 / r_norm ** 5
    common_xy = 5 * z ** 2 / r_norm ** 2 - 1
    ax = factor * x * common_xy
    ay = factor * y * common_xy
    az = factor * z * (5 * z ** 2 / r_norm ** 2 - 3)
    return np.array([ax, ay, az])


def _derivatives(state: np.ndarray) -> np.ndarray:
    """
    state = [x, y, z, vx, vy, vz]
    returns d/dt(state) = [vx, vy, vz, ax, ay, az]
    """
    r = state[:3]
    v = state[3:]
    r_norm = np.linalg.norm(r)
    a_grav = -MU / r_norm ** 3 * r
    a_j2   = _j2_accel(r)
    return np.concatenate([v, a_grav + a_j2])


def rk4_step(state: np.ndarray, dt: float) -> np.ndarray:
    """Single RK4 integration step. state=[x,y,z,vx,vy,vz], dt in seconds."""
    k1 = _derivatives(state)
    k2 = _derivatives(state + 0.5 * dt * k1)
    k3 = _derivatives(state + 0.5 * dt * k2)
    k4 = _derivatives(state + dt * k3)
    return state + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)


def propagate(r: list, v: list, duration_s: float, dt: float = 10.0) -> tuple[list, list]:
    """
    Propagate a single object forward by duration_s seconds.
    Returns (new_r, new_v) as plain Python lists.
    dt: integration step size in seconds (default 10s).
    """
    state = np.array(r + v, dtype=float)
    elapsed = 0.0
    while elapsed < duration_s:
        step = min(dt, duration_s - elapsed)
        state = rk4_step(state, step)
        elapsed += step
    return state[:3].tolist(), state[3:].tolist()


def propagate_with_history(
    r: list, v: list, duration_s: float, record_interval: float = 60.0, dt: float = 10.0
) -> list[dict]:
    """
    Propagate and record state every record_interval seconds.
    Returns list of {"t": elapsed_s, "r": [...], "v": [...]}
    Useful for TCA search and ground-track visualization.
    """
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
    """
    Apply an impulsive ΔV in ECI frame to velocity vector.
    Position is unchanged (impulsive burn assumption).
    """
    v_arr = np.array(v) + np.array(delta_v_eci)
    return v_arr.tolist()


def eci_to_geodetic(r_eci: list, gmst_rad: float) -> tuple[float, float, float]:
    """
    Convert ECI position to geodetic (lat_deg, lon_deg, alt_km).
    gmst_rad: Greenwich Mean Sidereal Time in radians.
    """
    x, y, z = r_eci
    # Rotate ECI → ECEF
    ecef_x =  x * np.cos(gmst_rad) + y * np.sin(gmst_rad)
    ecef_y = -x * np.sin(gmst_rad) + y * np.cos(gmst_rad)
    ecef_z =  z

    lon_rad = np.arctan2(ecef_y, ecef_x)
    p       = np.sqrt(ecef_x ** 2 + ecef_y ** 2)
    lat_rad = np.arctan2(ecef_z, p)          # simplified spherical
    alt_km  = np.sqrt(ecef_x**2 + ecef_y**2 + ecef_z**2) - RE

    return (
        float(np.degrees(lat_rad)),
        float(np.degrees(lon_rad)),
        float(alt_km),
    )


def compute_gmst(sim_time_iso: str) -> float:
    """
    Compute Greenwich Mean Sidereal Time (radians) from ISO timestamp.
    Uses simplified formula accurate to ~0.1 degrees for LEO purposes.
    """
    from datetime import datetime, timezone
    import math

    dt = datetime.fromisoformat(sim_time_iso.replace("Z", "+00:00"))
    # Julian date
    jd = (dt - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0 + 2451545.0
    T  = (jd - 2451545.0) / 36525.0
    gmst_deg = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
                + 0.000387933 * T ** 2) % 360.0
    return math.radians(gmst_deg)
