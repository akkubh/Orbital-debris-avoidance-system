"""
api/simulate.py
━━━━━━━━━━━━━━━
Simulation step & visualization snapshot for ACM
"""

import logging
import numpy as np
from fastapi import APIRouter
from datetime import datetime, timezone, timedelta

from models.schemas import (
    SimStepRequest, SimStepResponse, VisualizationSnapshot, SatelliteSnapshot,
)
from models.state_store import (
    state, save_state,
    INITIAL_FUEL_KG, EOL_FUEL_FRAC,
)
from physics.propagator import rk4_step
from physics.maneuver_calc import fuel_consumed, plan_graveyard_burn
from physics.conjunction import check_current_collisions

router = APIRouter()
log    = logging.getLogger("simulate")

PROPAGATION_DT = 10.0        # RK4 sub-step (seconds)
MU             = 398600.4418  # km³/s²


# FIX: _deriv moved out of the while loop — was being redefined on every
# sub-step iteration, which is wasteful and obscures the logic.
def _deriv(r_b, v_b):
    r_norms = np.linalg.norm(r_b, axis=1, keepdims=True)
    a_grav  = -MU / (r_norms ** 3) * r_b
    return v_b, a_grav


@router.post("/api/simulate/step", response_model=SimStepResponse)
async def simulate_step(payload: SimStepRequest):
    if state.sim_time is None:
        state.sim_time = datetime.now(timezone.utc).isoformat()

    step_s             = payload.step_seconds
    maneuvers_executed = 0
    collisions_found   = 0
    elapsed            = 0.0

    while elapsed < step_s:
        dt       = min(PROPAGATION_DT, step_s - elapsed)
        target_e = state.sim_epoch + dt

        # ── Execute pending burns ─────────────────────────────────────────────
        for burn in state.burns:
            if burn.executed:
                continue
            if burn.burn_time_epoch <= target_e:
                sat = state.objects.get(burn.satellite_id)
                if sat and sat.type == "SAT" and sat.status not in ("DEAD",):
                    _execute_burn(sat, burn)
                    maneuvers_executed += 1

        # ── Propagate satellites (full RK4 + J2) ─────────────────────────────
        sat_objects = [o for o in state.objects.values() if o.type == "SAT"]
        deb_objects = [o for o in state.objects.values() if o.type == "DEBRIS"]

        for obj in sat_objects:
            # FIX: ensure r and v are plain float lists before concatenation
            s = np.array(list(obj.r) + list(obj.v), dtype=float)
            s = rk4_step(s, dt)
            obj.r = s[:3].tolist()
            obj.v = s[3:].tolist()

        # ── Propagate debris (vectorised 2-body RK4, no J2) ──────────────────
        if deb_objects:
            rs = np.array([d.r for d in deb_objects], dtype=float)  # (N,3)
            vs = np.array([d.v for d in deb_objects], dtype=float)  # (N,3)

            v1, a1 = _deriv(rs,               vs)
            v2, a2 = _deriv(rs + 0.5*dt*v1,   vs + 0.5*dt*a1)
            v3, a3 = _deriv(rs + 0.5*dt*v2,   vs + 0.5*dt*a2)
            v4, a4 = _deriv(rs + dt*v3,        vs + dt*a3)

            rs_new = rs + (dt/6.0) * (v1 + 2*v2 + 2*v3 + v4)
            vs_new = vs + (dt/6.0) * (a1 + 2*a2 + 2*a3 + a4)

            for i, obj in enumerate(deb_objects):
                obj.r = rs_new[i].tolist()
                obj.v = vs_new[i].tolist()

        state.sim_epoch += dt
        elapsed         += dt

    base_dt        = datetime.fromisoformat(state.sim_time.replace("Z", "+00:00"))
    state.sim_time = (base_dt + timedelta(seconds=step_s)).isoformat()

    sats   = state.get_satellites()
    debris = state.get_debris()
    hits   = check_current_collisions(sats, debris)
    for hit in hits:
        collisions_found       += 1
        state.total_collisions += 1
        sat = state.objects.get(hit["sat_id"])
        if sat:
            sat.status = "DEAD"
        log.error(
            f"💥 COLLISION: {hit['sat_id']} ↔ {hit['deb_id']} | "
            f"distance {hit['distance_km']*1000:.1f} m"
        )

    _update_satellite_statuses()
    state.total_maneuvers_executed += maneuvers_executed

    pending_count = sum(1 for b in state.burns if not b.executed)
    log.info(
        f"⏱  Tick +{step_s:.0f}s → {state.sim_time} | "
        f"burns_fired={maneuvers_executed} | "
        f"burns_pending={pending_count} | "
        f"collisions={collisions_found} | "
        f"cdm_active={state.active_cdm_count()}"
    )

    save_state()

    return SimStepResponse(
        status="STEP_COMPLETE",
        new_timestamp=state.sim_time,
        collisions_detected=collisions_found,
        maneuvers_executed=maneuvers_executed,
    )


def _execute_burn(sat, burn):
    """Apply ΔV, deduct fuel. Keeps EOL status — does not reset to MANEUVERING."""
    dv_eci = np.array(burn.delta_v_eci, dtype=float)
    dv_mag = float(np.linalg.norm(dv_eci))

    dm          = fuel_consumed(dv_mag, sat.wet_mass)
    sat.fuel_kg = max(0.0, sat.fuel_kg - dm)
    sat.v       = (np.array(sat.v) + dv_eci).tolist()
    sat.last_burn_time = burn.burn_time_epoch
    burn.executed      = True

    # Only set MANEUVERING if satellite is not already EOL/DEAD
    if sat.status not in ("EOL", "DEAD"):
        sat.status = "MANEUVERING"

    log.info(
        f"🚀 BURN EXECUTED — {burn.burn_id} on {sat.id} | "
        f"ΔV={dv_mag*1000:.3f} m/s | fuel={sat.fuel_kg:.2f} kg | "
        f"status={sat.status}"
    )


def _update_satellite_statuses():
    """
    Status machine per tick:
      DEAD        → stays DEAD (skip all)
      EOL         → stays EOL (graveyard burn already scheduled once)
      fuel<=0     → force EOL immediately regardless of current status,
                    so a burn that depletes all fuel doesn't leave the
                    satellite stuck in MANEUVERING with 0 kg forever.
      fuel<5%     → transition to EOL, schedule graveyard burn ONCE
      MANEUVERING + no pending burns → NOMINAL
      otherwise   → NOMINAL
    """
    for sat in state.get_satellites():
        if sat.status == "DEAD":
            continue

        # FIX: force EOL if fuel is completely gone regardless of current status.
        # Previously a satellite that burned its last fuel stayed MANEUVERING
        # forever because the EOL check excluded MANEUVERING status.
        if sat.fuel_kg <= 0.0 and sat.status not in ("EOL", "DEAD"):
            sat.status = "EOL"
            log.warning(f"Satellite {sat.id} fuel depleted — forced to EOL")

        # ── EOL check ─────────────────────────────────────────────────────────
        # Only transition once: not already EOL and not mid-burn
        if sat.fuel_fraction < EOL_FUEL_FRAC and sat.status not in ("EOL", "MANEUVERING"):
            sat.status = "EOL"
            graveyard  = plan_graveyard_burn(sat, state.sim_epoch)
            if graveyard:
                base = datetime.fromisoformat(
                    state.sim_time.replace("Z", "+00:00")
                ) if state.sim_time else datetime.now(timezone.utc)
                burn_iso = (
                    base + timedelta(seconds=graveyard["burn_epoch"] - state.sim_epoch)
                ).isoformat()

                from models.state_store import ScheduledBurn
                state.burns.append(ScheduledBurn(
                    burn_id=graveyard["burn_id"],
                    satellite_id=sat.id,
                    burn_time_iso=burn_iso,
                    burn_time_epoch=graveyard["burn_epoch"],
                    delta_v_eci=graveyard["delta_v_eci"],
                ))
                log.warning(f"EOL graveyard burn scheduled for {sat.id}")

        # ── Clear MANEUVERING when all burns done ─────────────────────────────
        if sat.status == "MANEUVERING":
            if not state.pending_burns_for(sat.id):
                sat.status = "NOMINAL"


# ── Visualization snapshot ────────────────────────────────────────────────────

@router.get("/api/visualization/snapshot", response_model=VisualizationSnapshot)
async def visualization_snapshot():
    pending_counts: dict[str, int] = {}
    for b in state.burns:
        if not b.executed:
            pending_counts[b.satellite_id] = \
                pending_counts.get(b.satellite_id, 0) + 1

    satellites   = []
    debris_cloud = []

    for obj in state.objects.values():
        x, y, z = obj.r
        if obj.type == "SAT":
            satellites.append({
                "id":            obj.id,
                "x":             x,
                "y":             y,
                "z":             z,
                "fuel_kg":       round(obj.fuel_kg, 2),
                "status":        obj.status,
                "pending_burns": pending_counts.get(obj.id, 0),
            })
        else:
            debris_cloud.append({"id": obj.id, "x": x, "y": y, "z": z})

    return VisualizationSnapshot(
        timestamp=state.sim_time or "",
        satellites=satellites,
        debris=debris_cloud,
    )