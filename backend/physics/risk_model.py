"""
physics/risk_model.py
━━━━━━━━━━━━━━━━━━━━
Collision Risk Scoring for ACM

FIXES APPLIED:
  - SAFE_MISS_DISTANCE_KM removed (was defined but never used)
  - SAFE_MISS_KM imported from state_store — single source of truth
  - classify_risk now uses SAFE_MISS_KM as a hard floor: any event with
    miss < SAFE_MISS_KM is guaranteed at least HIGH regardless of score
"""

import math
import logging
import numpy as np
from typing import Any

from models.state_store import SAFE_MISS_KM

logger = logging.getLogger("ACM.RiskModel")

RISK_THRESHOLDS = {
    "CRITICAL": 50.0,
    "HIGH":     10.0,
    "MEDIUM":    2.0,
}


def calculate_relative_velocity(sat_v: np.ndarray, deb_v: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(sat_v) - np.asarray(deb_v)))


def calculate_risk_score(
    miss_distance_km: float,
    relative_velocity_kms: float,
    tca_seconds: float,
) -> float:
    miss_distance_km      = max(miss_distance_km,      0.0)
    relative_velocity_kms = max(relative_velocity_kms, 0.0)
    tca_seconds           = max(tca_seconds,           0.0)

    score = (
        (1.0 / (miss_distance_km + 0.001))
        * relative_velocity_kms
        * (1.0 / (tca_seconds + 1.0))
    )
    return round(score, 6)


def classify_risk(score: float, miss_distance_km: float = float("inf")) -> str:
    """
    FIX: added miss_distance_km parameter.
    Any event with miss < SAFE_MISS_KM is guaranteed at least HIGH,
    regardless of numeric score (fixes low-score but dangerously close events).
    """
    if miss_distance_km < SAFE_MISS_KM:
        if score >= RISK_THRESHOLDS["CRITICAL"]:
            return "CRITICAL"
        return "HIGH"

    if score >= RISK_THRESHOLDS["CRITICAL"]:
        return "CRITICAL"
    elif score >= RISK_THRESHOLDS["HIGH"]:
        return "HIGH"
    elif score >= RISK_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "LOW"


def evaluate_conjunction_event(event: Any) -> dict:
    if (hasattr(event, "sat_velocity") and hasattr(event, "deb_velocity")
            and event.sat_velocity is not None and event.deb_velocity is not None):
        rel_v = calculate_relative_velocity(
            np.asarray(event.sat_velocity),
            np.asarray(event.deb_velocity),
        )
    elif hasattr(event, "relative_velocity") and event.relative_velocity is not None:
        rel_v = float(event.relative_velocity)
    else:
        rel_v = 7.5
        logger.warning("[ACM] No velocity data — defaulting rel_v=7.5 km/s")

    miss_dist = float(getattr(event, "miss_distance", 1.0))
    tca_sec   = float(getattr(event, "tca_seconds",   3600.0))
    score     = calculate_risk_score(miss_dist, rel_v, tca_sec)
    level     = classify_risk(score, miss_dist)

    sat_id = getattr(event, "satellite_id", "???")
    deb_id = getattr(event, "debris_id",    "???")

    logger.info(
        f"[ACM] Risk {level} for {sat_id} vs {deb_id} | "
        f"score={score:.4f} | miss={miss_dist:.3f} km | "
        f"rel_v={rel_v:.3f} km/s | TCA={tca_sec:.0f}s"
    )
    return {"risk_score": score, "risk_level": level}