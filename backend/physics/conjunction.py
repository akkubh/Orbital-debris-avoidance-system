"""
Conjunction Assessment using KD-tree spatial indexing.
O(N log N) instead of O(N²).
All distances in km, times in seconds.
"""
import numpy as np
from scipy.spatial import KDTree
from dataclasses import dataclass
from typing import Optional

from physics.propagator import propagate, rk4_step

# Search radius for TCA refinement — anything closer than this gets TCA calc
BROAD_SEARCH_KM  = 50.0     # initial KD-tree query radius
CONJUNCTION_KM   = 0.100    # 100 m hard threshold
TCA_LOOKAHEAD_S  = 86400.0  # 24 hours
TCA_COARSE_DT    = 60.0     # 1 min steps for coarse TCA search
TCA_FINE_DT      = 1.0      # 1 sec steps for fine TCA refinement


@dataclass
class ConjunctionEvent:
    sat_id: str
    deb_id: str
    tca_offset_s: float      # seconds from NOW until closest approach
    miss_distance_km: float
    sat_r_at_tca: list
    deb_r_at_tca: list


def build_debris_tree(debris_objects: list) -> tuple[KDTree, list, np.ndarray]:
    """
    Build a KD-tree from debris positions.
    Returns (tree, [deb_id, ...], positions_array).
    """
    ids       = [d.id for d in debris_objects]
    positions = np.array([d.r for d in debris_objects], dtype=float)
    tree      = KDTree(positions)
    return tree, ids, positions


def screen_conjunctions(satellites: list, debris_objects: list) -> list[ConjunctionEvent]:
    """
    Two-phase conjunction screening:
    1. KD-tree broad phase — find debris within BROAD_SEARCH_KM of each satellite NOW
    2. Propagation narrow phase — compute TCA for each candidate pair

    Returns list of ConjunctionEvents sorted by miss distance.
    """
    if not debris_objects or not satellites:
        return []

    tree, deb_ids, deb_positions = build_debris_tree(debris_objects)
    events: list[ConjunctionEvent] = []

    for sat in satellites:
        sat_pos = np.array(sat.r, dtype=float)
        # Phase 1: broad KD-tree search
        candidate_indices = tree.query_ball_point(sat_pos, r=BROAD_SEARCH_KM)
        if not candidate_indices:
            continue

        for idx in candidate_indices:
            deb_id  = deb_ids[idx]
            deb_obj = next(d for d in debris_objects if d.id == deb_id)

            event = compute_tca(sat, deb_obj)
            if event is not None:
                events.append(event)

    events.sort(key=lambda e: e.miss_distance_km)
    return events


def compute_tca(sat, deb) -> Optional[ConjunctionEvent]:
    """
    Find Time of Closest Approach between sat and deb over 24 hours.
    Uses coarse scan followed by golden-section refinement.
    """
    sat_state = np.array(sat.r + sat.v, dtype=float)
    deb_state = np.array(deb.r + deb.v, dtype=float)

    # ── Coarse scan ────────────────────────────────────────────────────
    best_dist   = np.inf
    best_offset = 0.0
    n_steps     = int(TCA_LOOKAHEAD_S / TCA_COARSE_DT)

    cur_sat = sat_state.copy()
    cur_deb = deb_state.copy()

    for i in range(n_steps):
        dist = np.linalg.norm(cur_sat[:3] - cur_deb[:3])
        if dist < best_dist:
            best_dist   = dist
            best_offset = i * TCA_COARSE_DT
            best_sat_r  = cur_sat[:3].tolist()
            best_deb_r  = cur_deb[:3].tolist()
        cur_sat = rk4_step(cur_sat, TCA_COARSE_DT)
        cur_deb = rk4_step(cur_deb, TCA_COARSE_DT)

    # If never comes within 10 km, skip expensive refinement
    if best_dist > 10.0:
        return None

    # ── Fine refinement around best_offset ±1 coarse step ─────────────
    t_start = max(0.0, best_offset - TCA_COARSE_DT)
    t_end   = best_offset + TCA_COARSE_DT

    cur_sat = sat_state.copy()
    cur_deb = deb_state.copy()
    # Fast-forward to t_start
    steps_to_start = int(t_start / TCA_FINE_DT)
    for _ in range(steps_to_start):
        cur_sat = rk4_step(cur_sat, TCA_FINE_DT)
        cur_deb = rk4_step(cur_deb, TCA_FINE_DT)

    fine_range = int((t_end - t_start) / TCA_FINE_DT)
    for i in range(fine_range):
        dist = np.linalg.norm(cur_sat[:3] - cur_deb[:3])
        if dist < best_dist:
            best_dist   = dist
            best_offset = t_start + i * TCA_FINE_DT
            best_sat_r  = cur_sat[:3].tolist()
            best_deb_r  = cur_deb[:3].tolist()
        cur_sat = rk4_step(cur_sat, TCA_FINE_DT)
        cur_deb = rk4_step(cur_deb, TCA_FINE_DT)

    # Only emit event if within our conjunction threshold
    if best_dist > CONJUNCTION_KM:
        return None

    return ConjunctionEvent(
        sat_id           = sat.id,
        deb_id           = deb.id,
        tca_offset_s     = best_offset,
        miss_distance_km = best_dist,
        sat_r_at_tca     = best_sat_r,
        deb_r_at_tca     = best_deb_r,
    )


def check_current_collisions(satellites: list, debris_objects: list) -> list[dict]:
    """
    Fast current-time collision check (no propagation).
    Returns list of active collision dicts.
    """
    if not debris_objects:
        return []

    tree, deb_ids, _ = build_debris_tree(debris_objects)
    collisions = []

    for sat in satellites:
        sat_pos = np.array(sat.r, dtype=float)
        idxs = tree.query_ball_point(sat_pos, r=CONJUNCTION_KM)
        for idx in idxs:
            dist = np.linalg.norm(sat_pos - np.array(debris_objects[idx].r))
            collisions.append({
                "sat_id": sat.id,
                "deb_id": deb_ids[idx],
                "distance_km": round(dist, 4),
            })

    return collisions
