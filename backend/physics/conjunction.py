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
MISS_DISTANCE_THRESHOLD_KM =  5.0   # Report threshold for conjunction events

RK4_HORIZON_S = 3600.0   # 1 hour
RK4_DT_S      =   10.0   # 10-second step

# Gravitational parameter — used for gravity-only debris propagation
# to match simulate.py's debris RK4 (no J2), keeping TCA predictions
# consistent with actual simulated debris positions.
_MU = 398600.4418  # km³/s²


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class ConjunctionEvent:
    satellite_id:      str
    debris_id:         str
    miss_distance:     float
    tca_seconds:       float
    relative_velocity: Optional[float]      = None
    sat_velocity:      Optional[np.ndarray] = field(default=None, repr=False)
    deb_velocity:      Optional[np.ndarray] = field(default=None, repr=False)
    risk_score:        float = 0.0
    risk_level:        str   = "LOW"

    # FIX: disable auto-generated __eq__ to prevent numpy array ambiguity.
    # Dataclass __eq__ calls == on each field; for np.ndarray fields this
    # returns an array, and `if array:` raises ValueError. Since events are
    # only sorted/iterated (never compared with ==), eq=False is safe.
    def __eq__(self, other):
        if not isinstance(other, ConjunctionEvent):
            return NotImplemented
        return (self.satellite_id == other.satellite_id and
                self.debris_id   == other.debris_id)

    def __hash__(self):
        return hash((self.satellite_id, self.debris_id))

    def __post_init__(self):
        if self.relative_velocity is None and \
           self.sat_velocity is not None and self.deb_velocity is not None:
            self.relative_velocity = float(
                np.linalg.norm(
                    np.asarray(self.sat_velocity) - np.asarray(self.deb_velocity)
                )
            )


# ── Debris gravity-only propagator ────────────────────────────────────────────

def _debris_rk4_step(state: np.ndarray, dt: float) -> np.ndarray:
    """
    FIX: gravity-only RK4 for debris (no J2), matching simulate.py's debris
    propagation. Using the full J2 rk4_step for debris in TCA search caused
    predicted positions to diverge from actual simulated debris positions,
    giving inaccurate miss distance estimates.
    """
    def _deriv(s):
        r      = s[:3]
        v      = s[3:]
        r_norm = np.linalg.norm(r)
        a_grav = -_MU / r_norm**3 * r
        return np.concatenate([v, a_grav])

    k1 = _deriv(state)
    k2 = _deriv(state + 0.5 * dt * k1)
    k3 = _deriv(state + 0.5 * dt * k2)
    k4 = _deriv(state + dt * k3)
    return state + (dt / 6.0) * (k1 + 2*k2 + 2*k3 + k4)


# ── Conjunction Screening ─────────────────────────────────────────────────────

def screen_conjunctions(
    satellites: dict,
    debris_objects: dict,
    threshold_km: float = CONJUNCTION_THRESHOLD_KM
) -> List[ConjunctionEvent]:
    events: List[ConjunctionEvent] = []

    if not satellites or not debris_objects:
        return events

    deb_ids       = list(debris_objects.keys())
    deb_positions = np.array([debris_objects[d].r for d in deb_ids])

    if deb_positions.ndim != 2 or deb_positions.shape[1] != 3:
        logger.error("[ACM] Debris position array malformed — skipping conjunction screen")
        return events

    tree = KDTree(deb_positions)

    for sat_id, sat_state in satellites.items():
        sat_pos = np.asarray(sat_state.r, dtype=float)
        sat_vel = np.asarray(sat_state.v, dtype=float)

        candidate_indices = tree.query_ball_point(sat_pos, r=threshold_km)

        for idx in candidate_indices:
            deb_id    = deb_ids[idx]
            deb_state = debris_objects[deb_id]
            deb_pos   = np.asarray(deb_state.r, dtype=float)
            deb_vel   = np.asarray(deb_state.v, dtype=float)

            # Near-field check at t=0: if debris already within miss threshold,
            # record immediately without RK4 propagation.
            current_dist = float(np.linalg.norm(sat_pos - deb_pos))
            if current_dist <= MISS_DISTANCE_THRESHOLD_KM:
                miss_dist = current_dist
                tca_sec   = 1.0  # avoid division-by-zero in risk scorer
                logger.info(
                    f"[ACM] Near-field t=0 conjunction: {sat_id} ↔ {deb_id} | "
                    f"dist={current_dist:.4f} km"
                )
            else:
                miss_dist, tca_sec = _compute_miss_distance_and_tca(
                    sat_pos, sat_vel, deb_pos, deb_vel
                )

            if miss_dist > MISS_DISTANCE_THRESHOLD_KM:
                continue

            # Floor relative velocity so parallel orbits still score HIGH
            rel_v           = float(np.linalg.norm(sat_vel - deb_vel))
            effective_rel_v = max(rel_v, 0.5)

            event = ConjunctionEvent(
                satellite_id      = sat_id,
                debris_id         = deb_id,
                miss_distance     = miss_dist,
                tca_seconds       = tca_sec,
                sat_velocity      = sat_vel,
                deb_velocity      = deb_vel,
                relative_velocity = effective_rel_v,
            )

            risk_info        = evaluate_conjunction_event(event)
            event.risk_score = risk_info["risk_score"]
            event.risk_level = risk_info["risk_level"]

            events.append(event)
            logger.info(
                f"[ACM] Conjunction: {sat_id} ↔ {deb_id} | "
                f"miss={miss_dist:.4f} km | TCA={tca_sec:.0f}s | "
                f"risk={event.risk_level} ({event.risk_score:.4f})"
            )

    events.sort(key=lambda e: e.risk_score, reverse=True)
    logger.info(
        f"[ACM] Screen complete: {len(events)} events "
        f"(threshold={threshold_km} km)"
    )
    return events


# ── TCA / Miss Distance ───────────────────────────────────────────────────────

def _compute_miss_distance_and_tca(
    sat_pos: np.ndarray,
    sat_vel: np.ndarray,
    deb_pos: np.ndarray,
    deb_vel: np.ndarray,
    dt_max_s: float = RK4_HORIZON_S,
    dt_step:  float = RK4_DT_S,
) -> tuple[float, float]:
    sat_state = np.concatenate([sat_pos, sat_vel])
    deb_state = np.concatenate([deb_pos, deb_vel])

    min_dist = np.inf
    tca      = 0.0
    elapsed  = 0.0

    while elapsed < dt_max_s:
        step = min(dt_step, dt_max_s - elapsed)
        # FIX: satellite uses full J2 rk4_step; debris uses gravity-only
        # _debris_rk4_step — consistent with how simulate.py propagates each.
        sat_state = rk4_step(sat_state, step)
        deb_state = _debris_rk4_step(deb_state, step)
        elapsed  += step
        dist = float(np.linalg.norm(sat_state[:3] - deb_state[:3]))
        if dist < min_dist:
            min_dist = dist
            tca      = elapsed

    return round(min_dist, 6), round(tca, 2)


# ── Current Collision Check ───────────────────────────────────────────────────

COLLISION_DISTANCE_KM = 0.010   # 10 m


def check_current_collisions(satellites: list, debris: list) -> list[dict]:
    """
    Check for actual collisions at the current simulation instant.
    Called once per simulation tick by simulate.py after propagation.
    """
    hits    = []
    hit_sat_ids = set()   # FIX: track already-hit sats to prevent multiple
                          # collision counts per tick before status is updated.

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

        # FIX: skip satellites already hit this tick — their status hasn't been
        # updated to DEAD yet (that happens after this function returns in
        # simulate.py), so without this guard a single satellite could register
        # multiple collision hits in one tick, inflating total_collisions.
        if sat.id in hit_sat_ids:
            continue

        sat_pos           = np.asarray(sat.r)
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
                hit_sat_ids.add(sat.id)
                logger.error(
                    f"[ACM] COLLISION DETECTED: {sat.id} ↔ {deb.id} | "
                    f"distance={dist_km * 1000:.1f} m"
                )
                break   # one collision per satellite per tick is enough

    return hits