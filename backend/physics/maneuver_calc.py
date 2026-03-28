"""
physics/maneuver_calc.py
━━━━━━━━━━━━━━━━━━━━━━━
Maneuver Planning for ACM (Autonomous Constellation Manager)

FIXES APPLIED:
  - Single ISP/G0/MAX_DV — all imported from state_store (no more 220 vs 300 split)
  - SAFE_MISS_KM imported from state_store (no more independent copy)
  - DV_CANDIDATES extended to cover full 0.001–0.015 range so HIGH risk
    events can reach a safe miss distance without needing CRITICAL escalation
  - optimize_delta_v now called for both HIGH and CRITICAL in telemetry
  - plan_recovery_burn uses nominal_r/v correctly (already correct, kept)
  - validate_burn type annotation fixed to accept list or ndarray
"""

import math
import logging
import numpy as np
from typing import Optional

from models.state_store import (
    ISP, G0, MAX_DV_PER_BURN, SAFE_MISS_KM,
    DRY_MASS_KG, INITIAL_FUEL_KG, EOL_FUEL_FRAC,
    THRUSTER_COOLDOWN,
)

logger = logging.getLogger("ACM.ManeuverCalc")

# ── Constants ─────────────────────────────────────────────────────────────────

MU_EARTH = 398600.4418

# FIX: DV candidates now cover full thruster range 1–15 m/s
# Previously max was 8 m/s, leaving HIGH risk conjunctions under-powered
DV_CANDIDATES_KMS = [0.001, 0.002, 0.003, 0.005, 0.008, 0.010, 0.012, 0.015]

RTN_DIRECTIONS = {
    "RADIAL":      np.array([1.0,  0.0,  0.0]),
    "TRANSVERSE":  np.array([0.0,  1.0,  0.0]),
    "NORMAL":      np.array([0.0,  0.0,  1.0]),
    "RETROGRADE":  np.array([0.0, -1.0,  0.0]),
}

_GRAVEYARD_ALTITUDE_RAISE_KM = 300.0
_GRAVEYARD_DELAY_S           = 300.0


# ── RTN ↔ ECI ─────────────────────────────────────────────────────────────────

def rtn_to_eci(dv_rtn: np.ndarray, sat_pos: np.ndarray, sat_vel: np.ndarray) -> np.ndarray:
    r_hat = sat_pos / (np.linalg.norm(sat_pos) + 1e-12)
    h_vec = np.cross(sat_pos, sat_vel)
    n_hat = h_vec / (np.linalg.norm(h_vec) + 1e-12)
    t_hat = np.cross(n_hat, r_hat)
    rot   = np.column_stack([r_hat, t_hat, n_hat])
    return rot @ dv_rtn


# ── Fuel ──────────────────────────────────────────────────────────────────────

def fuel_consumed(dv_kms: float, wet_mass_kg: float) -> float:
    """Tsiolkovsky rocket equation. Uses single ISP from state_store."""
    v_e      = ISP * G0
    consumed = wet_mass_kg * (1.0 - math.exp(-dv_kms / v_e))
    return round(consumed, 6)


def estimate_fuel_cost(dv_kms: float, dry_mass_kg: float = DRY_MASS_KG) -> float:
    """
    FIX: now uses same ISP as fuel_consumed (was 220, now 300).
    Planning and execution fuel estimates are now consistent.
    """
    v_e      = ISP * G0
    delta_m  = dry_mass_kg * (1.0 - math.exp(-dv_kms / v_e))
    return round(delta_m, 4)


# ── Miss distance simulation ───────────────────────────────────────────────────

def _simulate_post_maneuver_miss(
    sat_pos: np.ndarray, sat_vel: np.ndarray,
    deb_pos: np.ndarray, deb_vel: np.ndarray,
    dv_eci: np.ndarray, tca_seconds: float,
    steps: int = 200,
) -> float:
    new_sat_vel = sat_vel + dv_eci
    dt          = max(tca_seconds, 300.0) / steps
    rel_pos     = sat_pos - deb_pos
    rel_vel     = new_sat_vel - deb_vel
    min_dist    = np.inf

    for i in range(steps + 1):
        t    = i * dt
        r    = rel_pos + rel_vel * t
        dist = float(np.linalg.norm(r))
        if dist < min_dist:
            min_dist = dist

    return round(min_dist, 6)


# ── Candidate generation ───────────────────────────────────────────────────────

def generate_candidate_maneuvers(
    sat_pos: np.ndarray, sat_vel: np.ndarray,
    deb_pos: np.ndarray, deb_vel: np.ndarray,
    tca_seconds: float, dry_mass_kg: float = DRY_MASS_KG,
) -> list[dict]:
    candidates = []

    for direction_name, rtn_unit in RTN_DIRECTIONS.items():
        for dv_kms in DV_CANDIDATES_KMS:
            dv_rtn    = rtn_unit * dv_kms
            dv_eci    = rtn_to_eci(dv_rtn, sat_pos, sat_vel)
            miss_dist = _simulate_post_maneuver_miss(
                sat_pos, sat_vel, deb_pos, deb_vel, dv_eci, tca_seconds
            )
            fuel_kg = estimate_fuel_cost(dv_kms, dry_mass_kg)
            is_safe = miss_dist >= SAFE_MISS_KM
            score   = (miss_dist * 1000.0) - (fuel_kg * 100.0)

            candidates.append({
                "direction":     direction_name,
                "dv_kms":        dv_kms,
                "dv_eci":        dv_eci,
                "miss_distance": miss_dist,
                "fuel_cost":     fuel_kg,
                "score":         score,
                "is_safe":       is_safe,
            })

    candidates.sort(key=lambda c: (not c["is_safe"], -c["score"]))
    return candidates


# ── Evasion burn ───────────────────────────────────────────────────────────────

def plan_evasion_burn(
    sat_pos: np.ndarray, sat_vel: np.ndarray,
    deb_pos: np.ndarray, deb_vel: np.ndarray,
    tca_seconds: float, dry_mass_kg: float = DRY_MASS_KG,
) -> Optional[dict]:
    candidates = generate_candidate_maneuvers(
        sat_pos, sat_vel, deb_pos, deb_vel, tca_seconds, dry_mass_kg
    )
    if not candidates:
        logger.error("[ACM] plan_evasion_burn: no candidates generated")
        return None

    best = candidates[0]
    if not best["is_safe"]:
        logger.warning(
            f"[ACM] plan_evasion_burn: best candidate miss={best['miss_distance']:.3f} km "
            f"below SAFE_MISS ({SAFE_MISS_KM} km)"
        )

    logger.info(
        f"[ACM] Evasion burn: dir={best['direction']} "
        f"dv={best['dv_kms']*1000:.1f} m/s "
        f"miss={best['miss_distance']:.3f} km "
        f"fuel={best['fuel_cost']:.4f} kg"
    )
    return {
        "direction":     best["direction"],
        "dv_kms":        best["dv_kms"],
        "dv_eci":        best["dv_eci"],
        "miss_distance": best["miss_distance"],
        "fuel_cost":     best["fuel_cost"],
        "is_safe":       best["is_safe"],
    }


# ── Optimize ΔV ───────────────────────────────────────────────────────────────

def optimize_delta_v(
    sat_pos: np.ndarray, sat_vel: np.ndarray,
    deb_pos: np.ndarray, deb_vel: np.ndarray,
    tca_seconds: float, direction_name: str = "TRANSVERSE",
    step_kms: float = 0.001,
) -> Optional[float]:
    rtn_unit = RTN_DIRECTIONS.get(direction_name, RTN_DIRECTIONS["TRANSVERSE"])
    dv_kms   = step_kms

    while dv_kms <= MAX_DV_PER_BURN + 1e-9:
        dv_rtn    = rtn_unit * dv_kms
        dv_eci    = rtn_to_eci(dv_rtn, sat_pos, sat_vel)
        miss_dist = _simulate_post_maneuver_miss(
            sat_pos, sat_vel, deb_pos, deb_vel, dv_eci, tca_seconds
        )
        fuel_kg = estimate_fuel_cost(dv_kms, DRY_MASS_KG)

        if miss_dist >= SAFE_MISS_KM:
            logger.info(
                f"[ACM] optimize_delta_v: dv={dv_kms*1000:.1f} m/s "
                f"achieves miss={miss_dist:.3f} km | fuel={fuel_kg:.4f} kg"
            )
            return round(dv_kms, 6)

        dv_kms = round(dv_kms + step_kms, 6)

    logger.warning(
        f"[ACM] optimize_delta_v: could not achieve safe miss within "
        f"MAX_DV={MAX_DV_PER_BURN*1000:.0f} m/s ({direction_name})"
    )
    return None


# ── validate_burn ─────────────────────────────────────────────────────────────

def validate_burn(
    dv_eci,           # list or np.ndarray
    sat,
    sim_epoch: float,
    burn_epoch: float,
) -> tuple[bool, str, float]:
    dv_mag = float(np.linalg.norm(np.asarray(dv_eci, dtype=float)))

    if dv_mag > MAX_DV_PER_BURN + 1e-9:
        reason = (
            f"ΔV {dv_mag*1000:.2f} m/s exceeds per-burn limit "
            f"of {MAX_DV_PER_BURN*1000:.0f} m/s"
        )
        logger.warning(f"[ACM] validate_burn REJECTED: {reason}")
        return False, reason, 0.0

    if sat.last_burn_time is not None:
        time_since = burn_epoch - sat.last_burn_time
        if time_since < THRUSTER_COOLDOWN:
            reason = (
                f"Thruster cooldown not expired: {time_since:.0f}s elapsed, "
                f"{THRUSTER_COOLDOWN:.0f}s required"
            )
            logger.warning(f"[ACM] validate_burn REJECTED: {reason}")
            return False, reason, 0.0

    eol_reserve_kg = EOL_FUEL_FRAC * INITIAL_FUEL_KG
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

    new_wet_mass = sat.wet_mass - fuel_needed
    logger.info(
        f"[ACM] validate_burn OK | dv={dv_mag*1000:.2f} m/s | "
        f"fuel_used={fuel_needed:.4f} kg | mass_after={new_wet_mass:.2f} kg"
    )
    return True, "", new_wet_mass


# ── Recovery burn ─────────────────────────────────────────────────────────────

def plan_recovery_burn(evasion_dv_eci: np.ndarray, sat=None) -> np.ndarray:
    """
    Return a velocity correction toward the satellite's nominal orbit.
    Uses nominal_r/v from SpaceObject (now always set on first telemetry).
    Falls back to negating evasion ΔV if nominal state unavailable.
    """
    if sat is not None:
        nominal_v = getattr(sat, "nominal_v", None)
        nominal_r = getattr(sat, "nominal_r", None)

        if nominal_v is not None and nominal_r is not None:
            current_v   = np.asarray(sat.v,    dtype=float)
            nom_v       = np.asarray(nominal_v, dtype=float)
            current_r   = np.asarray(sat.r,    dtype=float)
            nom_r       = np.asarray(nominal_r, dtype=float)

            v_error     = nom_v - current_v
            r_error     = nom_r - current_r
            r_error_mag = float(np.linalg.norm(r_error))

            if r_error_mag > 1e-6:
                r_hat = r_error / r_error_mag
                pos_correction = r_hat * min(r_error_mag * 1e-4, MAX_DV_PER_BURN * 0.5)
                recovery_dv    = v_error + pos_correction
            else:
                recovery_dv = v_error

            mag = float(np.linalg.norm(recovery_dv))
            if mag > MAX_DV_PER_BURN:
                recovery_dv = recovery_dv / mag * MAX_DV_PER_BURN

            logger.info(
                f"[ACM] Recovery burn (nominal-orbit correction): "
                f"{float(np.linalg.norm(recovery_dv))*1000:.2f} m/s | "
                f"pos_error={r_error_mag:.3f} km"
            )
            return recovery_dv

    recovery = -1.0 * np.asarray(evasion_dv_eci, dtype=float)
    logger.info(
        f"[ACM] Recovery burn (evasion negation fallback): "
        f"{float(np.linalg.norm(recovery))*1000:.2f} m/s"
    )
    return recovery


# ── Graveyard burn ─────────────────────────────────────────────────────────────

def plan_graveyard_burn(sat, sim_epoch: float) -> Optional[dict]:
    if sat.fuel_kg <= 0.0:
        logger.warning(f"[ACM] {sat.id} has no fuel — graveyard burn impossible")
        return None

    sat_pos = np.asarray(sat.r, dtype=float)
    sat_vel = np.asarray(sat.v, dtype=float)
    r_mag   = float(np.linalg.norm(sat_pos))
    v_mag   = float(np.linalg.norm(sat_vel))

    if r_mag < 1.0:
        logger.error(f"[ACM] {sat.id} degenerate position — skip graveyard")
        return None

    a1      = r_mag
    a2      = a1 + _GRAVEYARD_ALTITUDE_RAISE_KM
    v_transf = math.sqrt(MU_EARTH / a1) * (math.sqrt(2.0 * a2 / (a1 + a2)) - 1.0)
    dv_mag  = min(abs(v_transf), MAX_DV_PER_BURN)

    v_hat  = sat_vel / (v_mag + 1e-12)
    dv_eci = (v_hat * dv_mag).tolist()

    burn_epoch = sim_epoch + _GRAVEYARD_DELAY_S
    burn_id    = f"GRAV-{sat.id}-{int(sim_epoch)}"

    logger.warning(
        f"[ACM] Graveyard burn: {sat.id} | dv={dv_mag*1000:.2f} m/s | "
        f"raise={_GRAVEYARD_ALTITUDE_RAISE_KM:.0f} km | epoch={burn_epoch:.0f}s"
    )
    return {"burn_id": burn_id, "burn_epoch": burn_epoch, "delta_v_eci": dv_eci}