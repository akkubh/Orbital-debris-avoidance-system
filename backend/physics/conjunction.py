

import math
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional
from scipy.spatial import KDTree

from physics.risk_model import evaluate_conjunction_event
from physics.propagator import rk4_step

logger = logging.getLogger("ACM.Conjunction")

# ── Configuration ─────────────────────────────────────────────────────────────

CONJUNCTION_THRESHOLD_KM   = 25.0   # KD-tree first-pass screening radius (km)
MISS_DISTANCE_THRESHOLD_KM =  5.0   # Hard safety threshold for reporting

# FIX 1 — 24-hour propagation window and step size
RK4_HORIZON_S = 86400.0   # 24 hours in seconds
RK4_DT_S      =    60.0   # 60-second integration step

# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class ConjunctionEvent:
    satellite_id:      str
    debris_id:         str
    miss_distance:     float
    tca_seconds:       float
    relative_velocity: Optional[float]    = None
    sat_velocity:      Optional[np.ndarray] = field(default=None, repr=False)
    deb_velocity:      Optional[np.ndarray] = field(default=None, repr=False)
    risk_score:        float = 0.0
    risk_level:        str   = "LOW"

    def __post_init__(self):
        if self.relative_velocity is None and \
           self.sat_velocity is not None and self.deb_velocity is not None:
            self.relative_velocity = float(
                np.linalg.norm(
                    np.asarray(self.sat_velocity) - np.asarray(self.deb_velocity)
                )
            )


# ── Conjunction Screening ─────────────────────────────────────────────────────

def screen_conjunctions(
    satellites: dict,
    debris_objects: dict,
    threshold_km: float = CONJUNCTION_THRESHOLD_KM
) -> List[ConjunctionEvent]:
    """
    Screen all satellite–debris pairs for close approaches over 24 hours.

    Steps:
        1. Build KD-tree over debris positions (spatial pre-filter, unchanged)
        2. Query debris within threshold_km of each satellite
        3. Compute precise miss distance and TCA using RK4 propagation
           over a 86400-second (24-hour) window  ← FIX 1
        4. Evaluate collision risk via risk_model.evaluate_conjunction_event()
        5. Return events sorted by risk_score descending
    """
    events: List[ConjunctionEvent] = []

    if not satellites or not debris_objects:
        return events

    # ── Build KD-tree over current debris positions ───────────────────────────
    deb_ids       = list(debris_objects.keys())
    deb_positions = np.array([debris_objects[d].r for d in deb_ids])

    if deb_positions.ndim != 2 or deb_positions.shape[1] != 3:
        logger.error("[ACM] Debris position array malformed — skipping conjunction screen")
        return events

    tree = KDTree(deb_positions)

    # ── Screen each satellite ─────────────────────────────────────────────────
    for sat_id, sat_state in satellites.items():
        sat_pos = np.asarray(sat_state.r, dtype=float)
        sat_vel = np.asarray(sat_state.v, dtype=float)

        candidate_indices = tree.query_ball_point(sat_pos, r=threshold_km)

        for idx in candidate_indices:
            deb_id    = deb_ids[idx]
            deb_state = debris_objects[deb_id]
            deb_pos   = np.asarray(deb_state.r, dtype=float)
            deb_vel   = np.asarray(deb_state.v, dtype=float)

            # ── FIX 1: RK4-based TCA over 24-hour window ─────────────────────
            miss_dist, tca_sec = _compute_miss_distance_and_tca(
                sat_pos, sat_vel, deb_pos, deb_vel
            )

            if miss_dist > MISS_DISTANCE_THRESHOLD_KM:
                continue

            event = ConjunctionEvent(
                satellite_id=sat_id,
                debris_id=deb_id,
                miss_distance=miss_dist,
                tca_seconds=tca_sec,
                sat_velocity=sat_vel,
                deb_velocity=deb_vel,
            )

            risk_info        = evaluate_conjunction_event(event)
            event.risk_score = risk_info["risk_score"]
            event.risk_level = risk_info["risk_level"]

            events.append(event)
            logger.debug(
                f"[ACM] Conjunction: {sat_id} ↔ {deb_id} | "
                f"miss={miss_dist:.3f} km | TCA={tca_sec:.0f}s | "
                f"risk={event.risk_level} ({event.risk_score:.4f})"
            )

    events.sort(key=lambda e: e.risk_score, reverse=True)

    logger.info(
        f"[ACM] Conjunction screen complete: {len(events)} events "
        f"(threshold={threshold_km} km, horizon={RK4_HORIZON_S/3600:.0f}h)"
    )
    return events


# ── TCA / Miss Distance — RK4 24-hour propagation (FIX 1) ────────────────────

def _compute_miss_distance_and_tca(
    sat_pos: np.ndarray,
    sat_vel: np.ndarray,
    deb_pos: np.ndarray,
    deb_vel: np.ndarray,
    dt_max_s: float = RK4_HORIZON_S,
    dt_step:  float = RK4_DT_S,
) -> tuple[float, float]:
    """
    Compute miss distance and TCA by propagating both objects with RK4
    over a 24-hour window.

    FIX 1: Replaces the previous linear relative-motion model with
    full RK4 integration (with J2 perturbations) from physics/propagator.

    Args:
        sat_pos / sat_vel: Satellite state (km, km/s, ECI)
        deb_pos / deb_vel: Debris state   (km, km/s, ECI)
        dt_max_s:  Propagation horizon (s) — default 86400 (24 h)
        dt_step:   RK4 integration step size (s) — default 60 s

    Returns:
        (miss_distance_km, tca_seconds)
    """
    sat_state = np.concatenate([sat_pos, sat_vel])
    deb_state = np.concatenate([deb_pos, deb_vel])

    min_dist = np.inf
    tca      = 0.0
    elapsed  = 0.0

    while elapsed < dt_max_s:
        step = min(dt_step, dt_max_s - elapsed)

        sat_state = rk4_step(sat_state, step)
        deb_state = rk4_step(deb_state, step)
        elapsed  += step

        dist = float(np.linalg.norm(sat_state[:3] - deb_state[:3]))
        if dist < min_dist:
            min_dist = dist
            tca      = elapsed

    return round(min_dist, 6), round(tca, 2)


# ── Current Collision Check (required by api/simulate.py) ────────────────────

COLLISION_DISTANCE_KM = 0.1   # 100 m


def check_current_collisions(satellites: list, debris: list) -> list[dict]:
    """
    Check for actual collisions at the *current* simulation instant.
    Called once per simulation tick by simulate.py after propagation.
    """
    hits = []

    if not satellites or not debris:
        return hits

    deb_list      = list(debris)
    deb_positions = np.array([np.asarray(d.r) for d in deb_list])

    if deb_positions.ndim != 2 or deb_positions.shape[1] != 3:
        return hits

    tree = KDTree(deb_positions)

    for sat in satellites:
        if getattr(sat, "status", "NOMINAL") == "DEAD":
            continue

        sat_pos          = np.asarray(sat.r)
        candidate_indices = tree.query_ball_point(sat_pos, r=COLLISION_DISTANCE_KM)

        for idx in candidate_indices:
            deb     = deb_list[idx]
            deb_pos = np.asarray(deb.r)
            dist_km = float(np.linalg.norm(sat_pos - deb_pos))

            if dist_km <= COLLISION_DISTANCE_KM:
                hits.append({
                    "sat_id":      sat.id,
                    "deb_id":      deb.id,
                    "distance_km": round(dist_km, 6),
                })
                logger.error(
                    f"[ACM] COLLISION DETECTED: {sat.id} ↔ {deb.id} | "
                    f"distance={dist_km * 1000:.1f} m"
                )

    return hits