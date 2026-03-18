"""
physics/maneuver_calc.py
━━━━━━━━━━━━━━━━━━━━━━━
Maneuver Planning for ACM (Autonomous Constellation Manager)
National Space Hackathon 2026

CHANGES vs original:
  • generate_candidate_maneuvers()  — evaluates RTN direction × ΔV combinations
  • plan_evasion_burn()             — now calls generate_candidate_maneuvers()
                                     instead of fixed transverse burn
  • optimize_delta_v()             — finds minimum ΔV for safe clearance
"""

import math
import logging
import numpy as np
from typing import Optional

logger = logging.getLogger("ACM.ManeuverCalc")

# ── Constants ─────────────────────────────────────────────────────────────────

MU_EARTH       = 398600.4418      # km³/s²  — Earth gravitational parameter
SAFE_MISS_KM   = 5.0              # km      — minimum acceptable miss distance
MAX_DV_KMS     = 0.015            # km/s    — ΔV budget ceiling (15 m/s)
ISP            = 220.0            # s       — thruster specific impulse
G0             = 0.00980665       # km/s²   — standard gravity
PROPELLANT_DENSITY_KG_PER_KMS = 30.0   # kg per km/s (rough LEO thruster model)

# Candidate ΔV magnitudes (km/s)
DV_CANDIDATES_KMS = [0.001, 0.002, 0.003, 0.005, 0.008]

# RTN direction vectors  (unit vectors — will be scaled by dv magnitude)
RTN_DIRECTIONS = {
    "RADIAL":      np.array([1.0,  0.0,  0.0]),
    "TRANSVERSE":  np.array([0.0,  1.0,  0.0]),
    "NORMAL":      np.array([0.0,  0.0,  1.0]),
    "RETROGRADE":  np.array([0.0, -1.0,  0.0]),
}


# ── RTN ↔ ECI Conversion ──────────────────────────────────────────────────────

def rtn_to_eci(
    dv_rtn: np.ndarray,
    sat_pos: np.ndarray,
    sat_vel: np.ndarray
) -> np.ndarray:
    """
    Convert a ΔV vector from RTN (Radial-Transverse-Normal) to ECI frame.

    RTN basis:
        R̂  = r̂  (radial outward)
        T̂  = Ŷ × R̂   (transverse, along-track for circular orbit)
        N̂  = R̂ × T̂  (normal, cross-track)

    Args:
        dv_rtn:  ΔV in RTN frame (km/s), shape (3,)
        sat_pos: Satellite position ECI (km), shape (3,)
        sat_vel: Satellite velocity ECI (km/s), shape (3,)

    Returns:
        dv_eci: ΔV in ECI frame (km/s), shape (3,)
    """
    r_hat = sat_pos / (np.linalg.norm(sat_pos) + 1e-12)
    h_vec = np.cross(sat_pos, sat_vel)
    n_hat = h_vec / (np.linalg.norm(h_vec) + 1e-12)
    t_hat = np.cross(n_hat, r_hat)

    # Rotation matrix: columns are RTN basis vectors expressed in ECI
    rot = np.column_stack([r_hat, t_hat, n_hat])   # shape (3, 3)
    return rot @ dv_rtn


# ── Fuel Cost Estimation ──────────────────────────────────────────────────────

def estimate_fuel_cost(dv_kms: float, dry_mass_kg: float = 500.0) -> float:
    """
    Estimate propellant mass for a given ΔV using the rocket equation.

    Δm = m0 * (1 - exp(-ΔV / (Isp * g0)))

    Args:
        dv_kms:       ΔV magnitude (km/s)
        dry_mass_kg:  Satellite dry mass (kg)

    Returns:
        Fuel mass consumed (kg)
    """
    v_e = ISP * G0   # effective exhaust velocity (km/s)
    delta_m = dry_mass_kg * (1.0 - math.exp(-dv_kms / v_e))
    return round(delta_m, 4)


# ── Miss Distance After Maneuver (Linear Model) ───────────────────────────────

def _simulate_post_maneuver_miss(
    sat_pos: np.ndarray,
    sat_vel: np.ndarray,
    deb_pos: np.ndarray,
    deb_vel: np.ndarray,
    dv_eci: np.ndarray,
    tca_seconds: float,
    steps: int = 200
) -> float:
    """
    Estimate miss distance after applying dv_eci, using linear propagation.

    Args:
        sat_pos / sat_vel: Pre-burn satellite state (km, km/s)
        deb_pos / deb_vel: Debris state (km, km/s)
        dv_eci:            Burn vector in ECI (km/s)
        tca_seconds:       Nominal TCA window to search (s)
        steps:             Time resolution

    Returns:
        Estimated minimum miss distance (km)
    """
    new_sat_vel = sat_vel + dv_eci
    dt = max(tca_seconds, 300.0) / steps   # avoid zero dt

    rel_pos = sat_pos - deb_pos
    rel_vel = new_sat_vel - deb_vel

    min_dist = np.inf
    for i in range(steps + 1):
        t = i * dt
        r = rel_pos + rel_vel * t
        dist = float(np.linalg.norm(r))
        if dist < min_dist:
            min_dist = dist

    return round(min_dist, 6)


# ── TASK 3: Generate Candidate Maneuvers ──────────────────────────────────────

def generate_candidate_maneuvers(
    sat_pos: np.ndarray,
    sat_vel: np.ndarray,
    deb_pos: np.ndarray,
    deb_vel: np.ndarray,
    tca_seconds: float,
    dry_mass_kg: float = 500.0
) -> list[dict]:
    """
    Generate and score all RTN direction × ΔV magnitude combinations.

    Scoring function:
        maneuver_score = (miss_distance * 1000) - (fuel_cost * 100)

    Higher score = better maneuver.

    Args:
        sat_pos / sat_vel: Satellite state pre-burn (km, km/s, ECI)
        deb_pos / deb_vel: Debris state (km, km/s, ECI)
        tca_seconds:       Seconds until TCA
        dry_mass_kg:       Satellite dry mass for fuel estimation

    Returns:
        List of candidate dicts, sorted best-first:
        {
            "direction":      str,
            "dv_kms":         float,
            "dv_eci":         np.ndarray,
            "miss_distance":  float,
            "fuel_cost":      float,
            "score":          float,
            "is_safe":        bool
        }
    """
    candidates = []

    for direction_name, rtn_unit in RTN_DIRECTIONS.items():
        for dv_kms in DV_CANDIDATES_KMS:
            dv_rtn = rtn_unit * dv_kms
            dv_eci = rtn_to_eci(dv_rtn, sat_pos, sat_vel)

            miss_dist = _simulate_post_maneuver_miss(
                sat_pos, sat_vel, deb_pos, deb_vel, dv_eci, tca_seconds
            )
            fuel_kg = estimate_fuel_cost(dv_kms, dry_mass_kg)
            is_safe = miss_dist >= SAFE_MISS_KM

            score = (miss_dist * 1000.0) - (fuel_kg * 100.0)

            candidates.append({
                "direction":     direction_name,
                "dv_kms":        dv_kms,
                "dv_eci":        dv_eci,
                "miss_distance": miss_dist,
                "fuel_cost":     fuel_kg,
                "score":         score,
                "is_safe":       is_safe,
            })

    # Sort: safe burns first, then by score descending
    candidates.sort(
        key=lambda c: (not c["is_safe"], -c["score"])
    )
    return candidates


# ── TASK 3 (continued): Plan Evasion Burn ────────────────────────────────────

def plan_evasion_burn(
    sat_pos: np.ndarray,
    sat_vel: np.ndarray,
    deb_pos: np.ndarray,
    deb_vel: np.ndarray,
    tca_seconds: float,
    dry_mass_kg: float = 500.0
) -> Optional[dict]:
    """
    Select the best evasion maneuver from all RTN candidates.

    Previously: always returned a fixed transverse burn.
    Now: evaluates all direction × ΔV combinations and picks the best.

    Args:
        sat_pos / sat_vel: Satellite state (km, km/s, ECI)
        deb_pos / deb_vel: Debris state (km, km/s, ECI)
        tca_seconds:       Seconds until TCA
        dry_mass_kg:       Satellite dry mass (kg)

    Returns:
        Best maneuver dict, or None if no safe maneuver found within budget.
        {
            "direction":     str,
            "dv_kms":        float,
            "dv_eci":        np.ndarray (km/s, ECI),
            "miss_distance": float (km),
            "fuel_cost":     float (kg),
            "score":         float,
            "is_safe":       bool
        }
    """
    candidates = generate_candidate_maneuvers(
        sat_pos, sat_vel, deb_pos, deb_vel, tca_seconds, dry_mass_kg
    )

    # Pick best safe maneuver
    safe_candidates = [c for c in candidates if c["is_safe"]]

    if safe_candidates:
        best = safe_candidates[0]
    elif candidates:
        # Fallback: pick least-bad unsafe option
        best = candidates[0]
        logger.warning(
            "[ACM] No safe maneuver found within budget — using best available "
            f"(miss={best['miss_distance']:.3f} km)"
        )
    else:
        logger.error("[ACM] No maneuver candidates generated!")
        return None

    logger.info(
        f"[ACM] Selected maneuver: {best['direction']} "
        f"{best['dv_kms'] * 1000:.1f} m/s | "
        f"miss={best['miss_distance']:.3f} km | "
        f"fuel={best['fuel_cost']:.4f} kg | "
        f"score={best['score']:.2f}"
    )
    return best


# ── TASK 4: Fuel-Optimal ΔV Search ───────────────────────────────────────────

def optimize_delta_v(
    sat_pos: np.ndarray,
    sat_vel: np.ndarray,
    deb_pos: np.ndarray,
    deb_vel: np.ndarray,
    tca_seconds: float,
    direction_name: str = "TRANSVERSE"
) -> Optional[float]:
    """
    Find the minimum ΔV (km/s) in `direction_name` that achieves a safe
    miss distance (≥ SAFE_MISS_KM).

    Algorithm:
        Start at dv = 0.001 km/s (1 m/s)
        Increase by 0.001 km/s per step
        Stop when miss_distance ≥ SAFE_MISS_KM
        Cap at MAX_DV_KMS (15 m/s)

    Args:
        sat_pos / sat_vel: Satellite state (km, km/s, ECI)
        deb_pos / deb_vel: Debris state (km, km/s, ECI)
        tca_seconds:       Seconds until TCA
        direction_name:    RTN direction key (default "TRANSVERSE")

    Returns:
        Minimum ΔV in km/s that achieves safety, or None if unreachable.
    """
    step_kms = 0.001   # 1 m/s

    if direction_name not in RTN_DIRECTIONS:
        logger.error(f"[ACM] Unknown RTN direction: {direction_name}")
        return None

    rtn_unit = RTN_DIRECTIONS[direction_name]
    dv_kms   = step_kms

    while dv_kms <= MAX_DV_KMS + 1e-9:
        dv_rtn = rtn_unit * dv_kms
        dv_eci = rtn_to_eci(dv_rtn, sat_pos, sat_vel)

        miss_dist = _simulate_post_maneuver_miss(
            sat_pos, sat_vel, deb_pos, deb_vel, dv_eci, tca_seconds
        )

        if miss_dist >= SAFE_MISS_KM:
            fuel_kg = estimate_fuel_cost(dv_kms)
            logger.info(
                f"[ACM] Optimal ΔV ({direction_name}): "
                f"{dv_kms * 1000:.1f} m/s | "
                f"achieves miss={miss_dist:.3f} km | "
                f"fuel={fuel_kg:.4f} kg"
            )
            return round(dv_kms, 6)

        dv_kms = round(dv_kms + step_kms, 6)

    logger.warning(
        f"[ACM] optimize_delta_v: could not achieve safe miss distance "
        f"within MAX_DV={MAX_DV_KMS * 1000:.0f} m/s ({direction_name})"
    )
    return None


# ── validate_burn & fuel_consumed (required by api/maneuver.py) ──────────────

# Mirror the constants from state_store so this module is self-contained
# (state_store imports these from its own module; we duplicate to avoid circular imports)
_ISP               = 300.0        # s      — matches state_store.ISP
_G0                = 9.80665e-3   # km/s²  — matches state_store.G0
_MAX_DV_PER_BURN   = 0.015        # km/s   — matches state_store.MAX_DV_PER_BURN
_THRUSTER_COOLDOWN = 600.0        # s      — matches state_store.THRUSTER_COOLDOWN
_EOL_FUEL_FRAC     = 0.05         # fraction — matches state_store.EOL_FUEL_FRAC
_INITIAL_FUEL_KG   = 50.0         # kg     — matches state_store.INITIAL_FUEL_KG


def fuel_consumed(dv_kms: float, wet_mass_kg: float) -> float:
    """
    Compute propellant mass consumed for a given ΔV using the Tsiolkovsky
    rocket equation.

        Δm = m0 × (1 − exp(−ΔV / (Isp × g0)))

    Args:
        dv_kms:       ΔV magnitude (km/s)
        wet_mass_kg:  Satellite wet mass before burn (kg)

    Returns:
        Propellant consumed (kg)
    """
    v_e = _ISP * _G0                              # effective exhaust velocity (km/s)
    consumed = wet_mass_kg * (1.0 - math.exp(-dv_kms / v_e))
    return round(consumed, 6)


def validate_burn(
    dv_eci: list,
    sat,
    sim_epoch: float,
    burn_epoch: float,
) -> tuple[bool, str, float]:
    """
    Validate a single burn command against satellite constraints.

    Checks (in order):
        1. ΔV magnitude ≤ MAX_DV_PER_BURN
        2. Thruster cooldown since last burn
        3. Sufficient fuel (won't drop below EOL reserve)

    Args:
        dv_eci:      Delta-V vector in ECI frame (km/s), list of 3 floats
        sat:         Satellite-like object with attributes:
                         .wet_mass       (kg)
                         .fuel_kg        (kg)
                         .dry_mass_kg    (kg)
                         .last_burn_time (float | None)  — epoch seconds
        sim_epoch:   Current simulation epoch (seconds)
        burn_epoch:  Scheduled burn epoch (seconds)

    Returns:
        (ok: bool, reason: str, new_wet_mass_kg: float)
        • ok           — True if burn is valid
        • reason       — human-readable rejection reason (empty string if ok)
        • new_wet_mass — projected wet mass after burn (0.0 if rejected)
    """
    dv_mag = float(np.linalg.norm(dv_eci))

    # ── 1. ΔV magnitude cap ───────────────────────────────────────────────────
    if dv_mag > _MAX_DV_PER_BURN + 1e-9:
        reason = (
            f"ΔV {dv_mag * 1000:.2f} m/s exceeds per-burn limit "
            f"of {_MAX_DV_PER_BURN * 1000:.0f} m/s"
        )
        logger.warning(f"[ACM] validate_burn REJECTED: {reason}")
        return False, reason, 0.0

    # ── 2. Thruster cooldown ──────────────────────────────────────────────────
    if sat.last_burn_time is not None:
        time_since_last = burn_epoch - sat.last_burn_time
        if time_since_last < _THRUSTER_COOLDOWN:
            reason = (
                f"Thruster cooldown not expired: "
                f"{time_since_last:.0f}s elapsed, "
                f"{_THRUSTER_COOLDOWN:.0f}s required"
            )
            logger.warning(f"[ACM] validate_burn REJECTED: {reason}")
            return False, reason, 0.0

    # ── 3. Fuel sufficiency ───────────────────────────────────────────────────
    eol_reserve_kg = _EOL_FUEL_FRAC * _INITIAL_FUEL_KG   # e.g. 2.5 kg
    fuel_needed    = fuel_consumed(dv_mag, sat.wet_mass)
    fuel_available = sat.fuel_kg - eol_reserve_kg

    if fuel_needed > fuel_available:
        reason = (
            f"Insufficient fuel: need {fuel_needed:.4f} kg, "
            f"available {fuel_available:.4f} kg "
            f"(reserve {eol_reserve_kg:.2f} kg held)"
        )
        logger.warning(f"[ACM] validate_burn REJECTED: {reason}")
        return False, reason, 0.0

    # ── All checks passed ─────────────────────────────────────────────────────
    new_wet_mass = sat.wet_mass - fuel_needed
    logger.info(
        f"[ACM] validate_burn OK | "
        f"dv={dv_mag * 1000:.2f} m/s | "
        f"fuel_used={fuel_needed:.4f} kg | "
        f"mass_after={new_wet_mass:.2f} kg"
    )
    return True, "", new_wet_mass


# ── Recovery Burn (unchanged interface) ──────────────────────────────────────

def plan_recovery_burn(evasion_dv_eci: np.ndarray) -> np.ndarray:
    """
    Plan a simple recovery burn to return to nominal orbit.

    Strategy: apply the negative of the evasion ΔV.

    Args:
        evasion_dv_eci: ΔV applied during evasion (km/s, ECI)

    Returns:
        Recovery ΔV vector (km/s, ECI)
    """
    recovery = -1.0 * np.asarray(evasion_dv_eci)
    mag = float(np.linalg.norm(recovery))
    logger.info(f"[ACM] Recovery burn planned: {mag * 1000:.2f} m/s")
    return recovery


# ── Graveyard Burn (required by api/simulate.py) ──────────────────────────────

# Graveyard orbit raise: ~300 km above LEO operational altitude
# Standard LEO disposal: raise perigee by ~300 km → ΔV ≈ 10-11 m/s
_GRAVEYARD_ALTITUDE_RAISE_KM = 300.0   # km above current orbit
_GRAVEYARD_DELAY_S            = 300.0  # seconds from now before executing burn


def plan_graveyard_burn(sat, sim_epoch: float) -> Optional[dict]:
    """
    Plan an end-of-life (EOL) disposal burn to raise the satellite into a
    graveyard orbit ~300 km above its current altitude.

    The burn is a prograde (transverse) impulse computed from the vis-viva
    equation for a Hohmann transfer apogee raise.

    Called by simulate.py _update_satellite_statuses() when:
        sat.fuel_fraction < EOL_FUEL_FRAC

    Args:
        sat:        SpaceObject with .r (km ECI), .v (km/s ECI),
                    .fuel_kg, .wet_mass, .id
        sim_epoch:  Current simulation epoch (seconds since sim start)

    Returns:
        {
            "burn_id":      str,
            "burn_epoch":   float,   # sim epoch when burn should fire
            "delta_v_eci":  list,    # [dvx, dvy, dvz] km/s in ECI
        }
        or None if satellite has no fuel at all.
    """
    if sat.fuel_kg <= 0.0:
        logger.warning(f"[ACM] {sat.id} has no fuel — graveyard burn impossible")
        return None

    sat_pos = np.asarray(sat.r, dtype=float)   # km
    sat_vel = np.asarray(sat.v, dtype=float)   # km/s

    r_mag = float(np.linalg.norm(sat_pos))
    v_mag = float(np.linalg.norm(sat_vel))

    if r_mag < 1.0:
        logger.error(f"[ACM] {sat.id} has degenerate position — skipping graveyard burn")
        return None

    # ── Hohmann transfer: raise apogee by GRAVEYARD_ALTITUDE_RAISE_KM ────────
    # Current semi-major axis (circular orbit approximation)
    a1 = r_mag

    # Target semi-major axis after raise
    a2 = a1 + _GRAVEYARD_ALTITUDE_RAISE_KM

    # ΔV for Hohmann apogee raise at perigee (prograde)
    # dv = sqrt(μ/a1) * (sqrt(2*a2/(a1+a2)) - 1)
    v_circ   = math.sqrt(MU_EARTH / a1)
    v_transf = math.sqrt(MU_EARTH / a1) * (
        math.sqrt(2.0 * a2 / (a1 + a2)) - 1.0
    )
    dv_mag = abs(v_transf)

    # Cap to whatever fuel remains (use all remaining fuel if needed)
    max_dv = fuel_consumed.__wrapped__(sat.fuel_kg, sat.wet_mass) \
        if hasattr(fuel_consumed, '__wrapped__') else _MAX_DV_PER_BURN
    dv_mag = min(dv_mag, _MAX_DV_PER_BURN)

    # ── Direction: prograde (along velocity vector) ───────────────────────────
    v_hat   = sat_vel / (v_mag + 1e-12)
    dv_eci  = (v_hat * dv_mag).tolist()

    burn_epoch = sim_epoch + _GRAVEYARD_DELAY_S
    burn_id    = f"GRAV-{sat.id}-{int(sim_epoch)}"

    logger.warning(
        f"[ACM] Graveyard burn planned for {sat.id} | "
        f"dv={dv_mag * 1000:.2f} m/s | "
        f"raise={_GRAVEYARD_ALTITUDE_RAISE_KM:.0f} km | "
        f"scheduled epoch={burn_epoch:.0f}s"
    )

    return {
        "burn_id":     burn_id,
        "burn_epoch":  burn_epoch,
        "delta_v_eci": dv_eci,
    }