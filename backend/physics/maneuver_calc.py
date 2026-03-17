"""
Maneuver calculation engine.
- RTN ↔ ECI frame conversions
- Tsiolkovsky rocket equation fuel depletion
- Evasion burn planner (prograde phasing)
- Recovery burn planner (return to station-keeping box)
All units: km, km/s, kg, seconds.
"""
import numpy as np
import math
from typing import Optional

ISP              = 300.0          # seconds
G0               = 9.80665e-3    # km/s²
MAX_DV_KMS       = 0.015         # km/s per burn (= 15 m/s)
THRUSTER_COOLDOWN = 600.0        # seconds
SIGNAL_LATENCY   = 10.0          # seconds


# ── Frame conversions ─────────────────────────────────────────────────

def rtn_to_eci_matrix(r_vec: list, v_vec: list) -> np.ndarray:
    """
    Build 3×3 rotation matrix from RTN to ECI.
    R = radial (r̂), T = transverse (along-track), N = normal (out-of-plane)
    """
    r = np.array(r_vec, dtype=float)
    v = np.array(v_vec, dtype=float)

    R_hat = r / np.linalg.norm(r)
    N_cross = np.cross(r, v)
    N_hat = N_cross / np.linalg.norm(N_cross)
    T_hat = np.cross(N_hat, R_hat)

    # Columns are the RTN basis vectors expressed in ECI
    return np.column_stack([R_hat, T_hat, N_hat])


def rtn_to_eci(dv_rtn: list, r_vec: list, v_vec: list) -> list:
    """Convert a ΔV vector from RTN to ECI frame."""
    M = rtn_to_eci_matrix(r_vec, v_vec)
    result = M @ np.array(dv_rtn, dtype=float)
    return result.tolist()


def eci_to_rtn(dv_eci: list, r_vec: list, v_vec: list) -> list:
    """Convert a ΔV vector from ECI to RTN frame."""
    M = rtn_to_eci_matrix(r_vec, v_vec)
    result = M.T @ np.array(dv_eci, dtype=float)
    return result.tolist()


# ── Propulsion physics ─────────────────────────────────────────────────

def fuel_consumed(delta_v_kms: float, current_mass_kg: float) -> float:
    """
    Tsiolkovsky rocket equation.
    Returns propellant mass consumed (kg).
    delta_v_kms: magnitude in km/s
    """
    dv_ms = delta_v_kms * 1000.0          # convert to m/s for standard Isp formula
    exponent = dv_ms / (ISP * 9.80665)    # use SI g0 here
    dm = current_mass_kg * (1.0 - math.exp(-exponent))
    return dm


def validate_burn(delta_v_eci: list, satellite, current_sim_epoch: float,
                  burn_epoch: float) -> tuple[bool, str, float]:
    """
    Validate a burn command. Returns (is_valid, rejection_reason, projected_mass_kg).
    """
    dv_mag = float(np.linalg.norm(delta_v_eci))

    # 1. ΔV magnitude limit
    if dv_mag > MAX_DV_KMS:
        return False, f"|ΔV| {dv_mag*1000:.2f} m/s exceeds 15 m/s limit", 0.0

    # 2. Signal latency
    if burn_epoch < current_sim_epoch + SIGNAL_LATENCY:
        return False, f"Burn too soon — must be ≥ current_time + {SIGNAL_LATENCY}s", 0.0

    # 3. Thruster cooldown
    last_burn = satellite.last_burn_time
    if last_burn is not None and (burn_epoch - last_burn) < THRUSTER_COOLDOWN:
        wait = THRUSTER_COOLDOWN - (burn_epoch - last_burn)
        return False, f"Thruster cooldown: {wait:.0f}s remaining", 0.0

    # 4. Fuel check
    dm = fuel_consumed(dv_mag, satellite.wet_mass)
    if dm >= satellite.fuel_kg:
        return False, f"Insufficient fuel: need {dm:.2f} kg, have {satellite.fuel_kg:.2f} kg", 0.0

    projected_mass = satellite.wet_mass - dm
    return True, "OK", projected_mass


# ── Evasion burn planner ──────────────────────────────────────────────

def plan_evasion_burn(sat, tca_offset_s: float, miss_distance_km: float,
                      current_sim_epoch: float) -> Optional[dict]:
    """
    Plan a prograde/retrograde phasing burn to avoid conjunction.
    Strategy: small transverse (T) ΔV changes orbital period so the satellite
    arrives early or late at the conjunction point.

    Returns dict with burn parameters, or None if not feasible.
    """
    # Burn timing: as early as possible (latency + margin)
    burn_offset = SIGNAL_LATENCY + 30.0       # 30s planning margin
    burn_epoch  = current_sim_epoch + burn_offset

    # Check cooldown
    if sat.last_burn_time and (burn_epoch - sat.last_burn_time) < THRUSTER_COOLDOWN:
        burn_epoch = sat.last_burn_time + THRUSTER_COOLDOWN + SIGNAL_LATENCY

    # ΔV magnitude: scale with urgency, cap at 10 m/s to leave fuel for recovery
    # Closer approach → larger burn
    if miss_distance_km < 0.01:        # < 10 m — critical
        dv_mag = 0.008                  # 8 m/s
    elif miss_distance_km < 0.05:      # < 50 m
        dv_mag = 0.005                  # 5 m/s
    else:
        dv_mag = 0.003                  # 3 m/s

    # Prograde burn (positive T) moves satellite forward, increasing period slightly
    # This causes satellite to "arrive late" at conjunction point
    dv_rtn = [0.0, dv_mag, 0.0]
    dv_eci = rtn_to_eci(dv_rtn, sat.r, sat.v)

    # Validate
    ok, reason, proj_mass = validate_burn(dv_eci, sat, current_sim_epoch, burn_epoch)
    if not ok:
        # Try retrograde instead
        dv_rtn = [0.0, -dv_mag, 0.0]
        dv_eci = rtn_to_eci(dv_rtn, sat.r, sat.v)
        ok, reason, proj_mass = validate_burn(dv_eci, sat, current_sim_epoch, burn_epoch)
        if not ok:
            return None

    return {
        "burn_id":       f"EVASION_{sat.id}_{int(burn_epoch)}",
        "burn_epoch":    burn_epoch,
        "delta_v_eci":   dv_eci,
        "dv_magnitude":  dv_mag,
        "projected_mass_kg": proj_mass,
    }


def plan_recovery_burn(sat, nominal_r: list, nominal_v: list,
                       evasion_burn_epoch: float, current_sim_epoch: float) -> Optional[dict]:
    """
    Plan a recovery burn to return satellite to station-keeping box.
    Scheduled ~one orbit period after evasion burn (~5400s for LEO).
    Simple approach: apply reverse of the evasion ΔV (phasing return).
    """
    from physics.propagator import propagate

    # One orbital period later (approximate: T ≈ 2π√(a³/μ))
    MU = 398600.4418
    r_norm = float(np.linalg.norm(sat.r))
    v_norm = float(np.linalg.norm(sat.v))
    semi_major = 1.0 / (2.0/r_norm - v_norm**2/MU)
    orbital_period = 2 * math.pi * math.sqrt(semi_major**3 / MU)

    recovery_epoch = evasion_burn_epoch + orbital_period + THRUSTER_COOLDOWN

    # Propagate satellite to recovery time to get position/velocity
    duration = recovery_epoch - current_sim_epoch
    r_future, v_future = propagate(sat.r, sat.v, duration)

    # Propagate nominal slot to the same time
    r_nom_future, v_nom_future = propagate(nominal_r, nominal_v, duration)

    # ΔV to match nominal velocity (simplified — corrects velocity mismatch)
    dv_eci = (np.array(v_nom_future) - np.array(v_future)).tolist()
    dv_mag = float(np.linalg.norm(dv_eci))

    # Clamp to MAX_DV
    if dv_mag > MAX_DV_KMS:
        scale   = MAX_DV_KMS / dv_mag
        dv_eci  = [v * scale for v in dv_eci]
        dv_mag  = MAX_DV_KMS

    if dv_mag < 1e-6:
        return None   # already on nominal trajectory

    # Create a temporary state snapshot for validation
    class TempSat:
        def __init__(self, s):
            self.last_burn_time = evasion_burn_epoch
            self.wet_mass  = s.wet_mass - fuel_consumed(
                float(np.linalg.norm(s.v)), s.wet_mass)
            self.fuel_kg   = s.fuel_kg
            self.r         = r_future
            self.v         = v_future

    ok, reason, proj_mass = validate_burn(dv_eci, TempSat(sat), current_sim_epoch, recovery_epoch)
    if not ok:
        return None

    return {
        "burn_id":           f"RECOVERY_{sat.id}_{int(recovery_epoch)}",
        "burn_epoch":        recovery_epoch,
        "delta_v_eci":       dv_eci,
        "dv_magnitude":      dv_mag,
        "projected_mass_kg": proj_mass,
    }


# ── EOL graveyard orbit ───────────────────────────────────────────────

def plan_graveyard_burn(sat, current_sim_epoch: float) -> Optional[dict]:
    """
    When fuel < 5% of initial, deorbit to graveyard orbit.
    Apply retrograde burn to lower perigee below 200 km (will naturally decay).
    """
    burn_epoch = current_sim_epoch + SIGNAL_LATENCY + 60.0

    # Retrograde burn — maximum available ΔV
    available_dv = min(sat.fuel_kg * ISP * G0 / sat.wet_mass, MAX_DV_KMS)
    dv_rtn = [0.0, -available_dv, 0.0]   # retrograde
    dv_eci = rtn_to_eci(dv_rtn, sat.r, sat.v)

    return {
        "burn_id":     f"GRAVEYARD_{sat.id}_{int(burn_epoch)}",
        "burn_epoch":  burn_epoch,
        "delta_v_eci": dv_eci,
        "dv_magnitude": available_dv,
        "reason":      "EOL_FUEL_CRITICAL",
    }
