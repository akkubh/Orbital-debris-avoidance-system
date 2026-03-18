"""
physics/conjunction.py
━━━━━━━━━━━━━━━━━━━━━
Conjunction Detection for ACM (Autonomous Constellation Manager)
National Space Hackathon 2026

Uses KD-tree spatial indexing for efficient pairwise screening.
Now augmented with intelligent collision risk scoring via risk_model.py.

CHANGES vs original:
  • ConjunctionEvent dataclass extended with risk_score, risk_level
  • screen_conjunctions() calls evaluate_conjunction_event() per event
  • Events are sorted by risk_score descending (highest risk first)
"""

import math
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional
from scipy.spatial import KDTree

# ── Risk model integration (NEW) ─────────────────────────────────────────────
from physics.risk_model import evaluate_conjunction_event

logger = logging.getLogger("ACM.Conjunction")

# ── Configuration ─────────────────────────────────────────────────────────────

CONJUNCTION_THRESHOLD_KM = 25.0   # KD-tree first-pass screening radius (km)
MISS_DISTANCE_THRESHOLD_KM = 5.0  # Hard safety threshold for reporting

# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class ConjunctionEvent:
    """
    Represents a potential collision event between a satellite and debris.

    Fields added in this update:
        risk_score  — numeric risk value from risk_model.py
        risk_level  — "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    """
    satellite_id:      str
    debris_id:         str
    miss_distance:     float          # km — closest approach distance
    tca_seconds:       float          # seconds until TCA
    relative_velocity: Optional[float] = None   # km/s

    # Optional velocity vectors for richer risk scoring
    sat_velocity:      Optional[np.ndarray] = field(default=None, repr=False)
    deb_velocity:      Optional[np.ndarray] = field(default=None, repr=False)

    # ── NEW: Risk fields (populated by screen_conjunctions) ──────────────────
    risk_score:        float = 0.0
    risk_level:        str   = "LOW"

    def __post_init__(self):
        # Coerce None velocities to float
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
    Screen all satellite–debris pairs for close approaches using a KD-tree.

    Steps:
        1. Build KD-tree over debris positions
        2. Query for debris within `threshold_km` of each satellite
        3. Compute precise miss distance and TCA for candidates
        4. Evaluate collision risk via risk_model.evaluate_conjunction_event()
        5. Return events sorted by risk_score descending

    Args:
        satellites:    dict of {sat_id: SatelliteState}
        debris_objects: dict of {deb_id: DebrisState}
        threshold_km:  KD-tree screening radius (km)

    Returns:
        List[ConjunctionEvent] sorted highest-risk first
    """
    events: List[ConjunctionEvent] = []

    if not satellites or not debris_objects:
        return events

    # ── Build KD-tree over debris positions ───────────────────────────────────
    deb_ids       = list(debris_objects.keys())
    deb_positions = np.array([
        debris_objects[d].r for d in deb_ids
    ])  # shape: (N_deb, 3) in km

    if deb_positions.ndim != 2 or deb_positions.shape[1] != 3:
        logger.error("[ACM] Debris position array malformed — skipping conjunction screen")
        return events

    tree = KDTree(deb_positions)

    # ── Screen each satellite ─────────────────────────────────────────────────
    for sat_id, sat_state in satellites.items():
        sat_pos = np.asarray(sat_state.r)   # km, ECI
        sat_vel = np.asarray(sat_state.v)   # km/s, ECI

        # KD-tree first-pass: find debris within threshold
        candidate_indices = tree.query_ball_point(sat_pos, r=threshold_km)

        for idx in candidate_indices:
            deb_id    = deb_ids[idx]
            deb_state = debris_objects[deb_id]
            deb_pos   = np.asarray(deb_state.r)
            deb_vel   = np.asarray(deb_state.v)

            # ── Precise miss distance & TCA ───────────────────────────────────
            miss_dist, tca_sec = _compute_miss_distance_and_tca(
                sat_pos, sat_vel, deb_pos, deb_vel
            )

            if miss_dist > MISS_DISTANCE_THRESHOLD_KM:
                continue   # Not a reportable conjunction

            # ── Build event ───────────────────────────────────────────────────
            event = ConjunctionEvent(
                satellite_id=sat_id,
                debris_id=deb_id,
                miss_distance=miss_dist,
                tca_seconds=tca_sec,
                sat_velocity=sat_vel,
                deb_velocity=deb_vel,
            )

            # ── UPDATED: Attach risk score & level ────────────────────────────
            risk_info = evaluate_conjunction_event(event)
            event.risk_score = risk_info["risk_score"]
            event.risk_level = risk_info["risk_level"]

            events.append(event)
            logger.debug(
                f"[ACM] Conjunction: {sat_id} ↔ {deb_id} | "
                f"miss={miss_dist:.3f} km | TCA={tca_sec:.0f}s | "
                f"risk={event.risk_level} ({event.risk_score:.4f})"
            )

    # ── Sort by highest risk first (NEW) ──────────────────────────────────────
    events.sort(key=lambda e: e.risk_score, reverse=True)

    logger.info(
        f"[ACM] Conjunction screen complete: {len(events)} events "
        f"(threshold={threshold_km} km)"
    )
    return events


# ── TCA / Miss Distance Computation ──────────────────────────────────────────

def _compute_miss_distance_and_tca(
    sat_pos: np.ndarray,
    sat_vel: np.ndarray,
    deb_pos: np.ndarray,
    deb_vel: np.ndarray,
    dt_max_s: float = 3600.0,
    steps: int = 360
) -> tuple[float, float]:
    """
    Estimate miss distance and TCA by linear propagation over a short window.

    Uses a linear relative-motion model (valid for short time horizons).
    For high-fidelity work the RK4 propagator should be used instead.

    Args:
        sat_pos / sat_vel: Satellite state (km, km/s, ECI)
        deb_pos / deb_vel: Debris state (km, km/s, ECI)
        dt_max_s:  Time window to search (seconds)
        steps:     Number of time steps

    Returns:
        (miss_distance_km, tca_seconds)
    """
    dt = dt_max_s / steps
    min_dist = np.inf
    tca = 0.0

    rel_pos = sat_pos - deb_pos
    rel_vel = sat_vel - deb_vel

    for i in range(steps + 1):
        t = i * dt
        r = rel_pos + rel_vel * t
        dist = float(np.linalg.norm(r))
        if dist < min_dist:
            min_dist = dist
            tca = t

    return round(min_dist, 6), round(tca, 2)


# ── Current Collision Check (required by api/simulate.py) ────────────────────

# Hard collision threshold — objects closer than this are considered a hit
COLLISION_DISTANCE_KM = 0.1   # 100 m  (matches state_store.CONJUNCTION_DIST)


def check_current_collisions(
    satellites: list,
    debris: list,
) -> list[dict]:
    """
    Check for actual collisions at the *current* simulation instant.

    Unlike screen_conjunctions() which looks ahead in time, this function
    checks present-moment distances only. Called once per simulation tick
    by simulate.py after propagation.

    Args:
        satellites: list of SpaceObject (type == "SAT")  with .r, .id
        debris:     list of SpaceObject (type == "DEBRIS") with .r, .id

    Returns:
        List of collision dicts for every pair within COLLISION_DISTANCE_KM:
        [
            {
                "sat_id":      str,
                "deb_id":      str,
                "distance_km": float,
            },
            ...
        ]
    """
    hits = []

    if not satellites or not debris:
        return hits

    # Build KD-tree over current debris positions for fast lookup
    deb_list      = list(debris)   # accepts both list and dict values
    deb_positions = np.array([np.asarray(d.r) for d in deb_list])

    if deb_positions.ndim != 2 or deb_positions.shape[1] != 3:
        return hits

    tree = KDTree(deb_positions)

    for sat in satellites:
        if getattr(sat, "status", "NOMINAL") == "DEAD":
            continue   # already dead, skip

        sat_pos = np.asarray(sat.r)

        # Query for any debris within collision threshold
        candidate_indices = tree.query_ball_point(sat_pos, r=COLLISION_DISTANCE_KM)

        for idx in candidate_indices:
            deb      = deb_list[idx]
            deb_pos  = np.asarray(deb.r)
            dist_km  = float(np.linalg.norm(sat_pos - deb_pos))

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