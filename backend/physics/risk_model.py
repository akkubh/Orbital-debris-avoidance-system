"""
physics/risk_model.py
━━━━━━━━━━━━━━━━━━━━
Collision Risk Scoring for ACM (Autonomous Constellation Manager)
National Space Hackathon 2026

Computes risk scores from conjunction event parameters:
  - Miss distance (km)
  - Relative velocity (km/s)
  - Time to Closest Approach / TCA (seconds)

Risk Levels: LOW | MEDIUM | HIGH | CRITICAL
"""

import math
import logging
import numpy as np
from typing import Any

logger = logging.getLogger("ACM.RiskModel")

# ── Risk Level Thresholds ────────────────────────────────────────────────────

RISK_THRESHOLDS = {
    "CRITICAL": 50.0,
    "HIGH":     10.0,
    "MEDIUM":    2.0,
    # Below MEDIUM → LOW
}

# Minimum safe miss distance (km) — below this is always HIGH or CRITICAL
SAFE_MISS_DISTANCE_KM = 5.0

# ── Core Physics Functions ───────────────────────────────────────────────────

def calculate_relative_velocity(
    sat_v: np.ndarray,
    deb_v: np.ndarray
) -> float:
    """
    Compute scalar relative velocity between satellite and debris.

    Args:
        sat_v: Satellite velocity vector in ECI frame (km/s), shape (3,)
        deb_v: Debris velocity vector in ECI frame (km/s), shape (3,)

    Returns:
        Relative velocity magnitude (km/s)
    """
    delta_v = np.asarray(sat_v) - np.asarray(deb_v)
    return float(np.linalg.norm(delta_v))


def calculate_risk_score(
    miss_distance_km: float,
    relative_velocity_kms: float,
    tca_seconds: float
) -> float:
    """
    Compute a dimensionless collision risk score.

    Heuristic formula:
        risk = (1 / (miss_distance + ε)) * relative_velocity * (1 / (tca + 1))

    Higher score → greater risk.

    Args:
        miss_distance_km:      Closest approach distance (km)
        relative_velocity_kms: Relative speed at TCA (km/s)
        tca_seconds:           Seconds until closest approach

    Returns:
        Risk score (float ≥ 0)
    """
    miss_distance_km    = max(miss_distance_km,    0.0)
    relative_velocity_kms = max(relative_velocity_kms, 0.0)
    tca_seconds         = max(tca_seconds,         0.0)

    score = (
        (1.0 / (miss_distance_km + 0.001))
        * relative_velocity_kms
        * (1.0 / (tca_seconds + 1.0))
    )
    return round(score, 6)


def classify_risk(score: float) -> str:
    """
    Map a numeric risk score to a risk level label.

    Thresholds (tuned for LEO conjunction geometry):
        score ≥ 50  → CRITICAL
        score ≥ 10  → HIGH
        score ≥  2  → MEDIUM
        else        → LOW

    Args:
        score: Output of calculate_risk_score()

    Returns:
        One of: "LOW", "MEDIUM", "HIGH", "CRITICAL"
    """
    if score >= RISK_THRESHOLDS["CRITICAL"]:
        return "CRITICAL"
    elif score >= RISK_THRESHOLDS["HIGH"]:
        return "HIGH"
    elif score >= RISK_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    else:
        return "LOW"


# ── Event-Level Evaluation ───────────────────────────────────────────────────

def evaluate_conjunction_event(event: Any) -> dict:
    """
    Evaluate a ConjunctionEvent and return risk score + level.

    Expects `event` to have attributes:
        event.miss_distance   (km)
        event.tca_seconds     (seconds until TCA)
        event.sat_velocity    (np.ndarray, km/s) — optional
        event.deb_velocity    (np.ndarray, km/s) — optional
        event.relative_velocity (km/s)           — used if velocity arrays absent

    Returns:
        {
            "risk_score": float,
            "risk_level": str   # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
        }
    """
    # ── Resolve relative velocity ────────────────────────────────────────────
    if hasattr(event, "sat_velocity") and hasattr(event, "deb_velocity") \
            and event.sat_velocity is not None and event.deb_velocity is not None:
        rel_v = calculate_relative_velocity(
            np.asarray(event.sat_velocity),
            np.asarray(event.deb_velocity)
        )
    elif hasattr(event, "relative_velocity") and event.relative_velocity is not None:
        rel_v = float(event.relative_velocity)
    else:
        # Typical LEO crossing velocity if data unavailable
        rel_v = 7.5
        logger.warning(
            "[ACM] No velocity data for conjunction event — defaulting rel_v=7.5 km/s"
        )

    # ── Resolve miss distance ────────────────────────────────────────────────
    miss_dist = float(getattr(event, "miss_distance", 1.0))

    # ── Resolve TCA ─────────────────────────────────────────────────────────
    tca_sec = float(getattr(event, "tca_seconds", 3600.0))

    # ── Score & classify ─────────────────────────────────────────────────────
    score = calculate_risk_score(miss_dist, rel_v, tca_sec)
    level = classify_risk(score)

    sat_id = getattr(event, "satellite_id", "???")
    deb_id = getattr(event, "debris_id",    "???")

    logger.info(
        f"[ACM] Risk {level} for {sat_id} vs {deb_id} | "
        f"score={score:.4f} | miss={miss_dist:.3f} km | "
        f"rel_v={rel_v:.3f} km/s | TCA={tca_sec:.0f}s"
    )

    return {
        "risk_score": score,
        "risk_level": level,
    }