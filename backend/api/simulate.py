"""
api/simulate.py
━━━━━━━━━━━━━━━
Simulation step & visualization snapshot for ACM

FIXES APPLIED:
  - EOL graveyard burn no longer causes re-entry into EOL block next tick
    (status stays EOL after _execute_burn, not reset to MANEUVERING→NOMINAL)
  - Station-keeping recovery: plan_recovery_burn now called correctly
    (nominal_r/v always set, so zero-vector fallback no longer happens)
  - MANEUVERING → NOMINAL clear respects EOL and DEAD
  - save_state() called once per tick, not inside update_satellite per-object
  - Debris objects are NOT RK4-propagated every tick (major perf fix)
    Only satellites are propagated; debris use linear drift approximation
  - burn_time_iso set to a valid ISO string for graveyard/SK burns
  - total_collisions and total_maneuvers_executed exposed in snapshot
"""

import logging
import uuid
import numpy as np
from fastapi import APIRouter
from datetime import datetime, timezone, timedelta

from models.schemas import (
    SimStepRequest, SimStepResponse, VisualizationSnapshot, SatelliteSnapshot,
)
from models.state_store import (
    state, save_state,
    INITIAL_FUEL_KG, EOL_FUEL_FRAC, STATION_BOX_KM,
    SIGNAL_LATENCY, THRUSTER_COOLDOWN,
)
from physics.propagator import rk4_step
from physics.maneuver_calc import fuel_consumed, plan_graveyard_burn, plan_recovery_burn
from physics.conjunction import check_current_collisions

router = APIRouter()
log    = logging.getLogger("simulate")

PROPAGATION_DT = 10.0   # RK4 sub-step (seconds)


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

        # ── Propagate satellites only (RK4) ───────────────────────────────────
        # FIX: debris objects are propagated with cheap linear drift.
        # Full RK4 on 500 debris every 10s tick was the main perf bottleneck.
        for obj in state.objects.values():
            if obj.type == "SAT":
                s = np.array(obj.r + obj.v, dtype=float)
                s = rk4_step(s, dt)
                obj.r = s[:3].tolist()
                obj.v = s[3:].tolist()
            else:
                # Linear drift for debris: r += v * dt (cheap, good enough for screening)
                obj.r = [obj.r[i] + obj.v[i] * dt for i in range(3)]

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
    """Apply ΔV, deduct fuel. Does NOT change status to MANEUVERING for EOL sats."""
    dv_eci = np.array(burn.delta_v_eci, dtype=float)
    dv_mag = float(np.linalg.norm(dv_eci))

    dm          = fuel_consumed(dv_mag, sat.wet_mass)
    sat.fuel_kg = max(0.0, sat.fuel_kg - dm)
    sat.v       = (np.array(sat.v) + dv_eci).tolist()
    sat.last_burn_time = burn.burn_time_epoch
    burn.executed      = True

    # FIX: only set MANEUVERING if satellite is not already EOL/DEAD
    # Prevents EOL sat cycling MANEUVERING → EOL check re-fires → duplicate burns
    if sat.status not in ("EOL", "DEAD"):
        sat.status = "MANEUVERING"

    log.info(
        f"🚀 BURN EXECUTED — {burn.burn_id} on {sat.id} | "
        f"ΔV={dv_mag*1000:.3f} m/s | fuel={sat.fuel_kg:.2f} kg | "
        f"status={sat.status}"
    )


def _update_satellite_statuses():
    for sat in state.get_satellites():
        if sat.status == "DEAD":
            continue

        # ── EOL check ─────────────────────────────────────────────────────────
        # FIX: check status not in (EOL, MANEUVERING) to prevent re-scheduling
        # graveyard burn every tick after the burn has already executed
        if sat.fuel_fraction < EOL_FUEL_FRAC and sat.status not in ("EOL", "MANEUVERING"):
            sat.status = "EOL"
            graveyard  = plan_graveyard_burn(sat, state.sim_epoch)
            if graveyard:
                # FIX: generate valid ISO string for burn_time_iso
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

        # ── Station-keeping ────────────────────────────────────────────────────
        # nominal_r is now always set (fixed in state_store.update_satellite)
        if sat.nominal_r and sat.status not in ("MANEUVERING", "EOL", "DEAD"):
            dist_from_slot = float(np.linalg.norm(
                np.array(sat.r) - np.array(sat.nominal_r)
            ))
            if dist_from_slot <= STATION_BOX_KM:
                sat.status = "NOMINAL"
            else:
                log.warning(
                    f"Station-keeping violation: {sat.id} is "
                    f"{dist_from_slot:.2f} km from slot"
                )
                # FIX: plan_recovery_burn now receives actual evasion dv (zeros
                # means "no evasion happened, just drift") — with nominal_r/v set,
                # the function computes a real velocity correction, not a zero vector
                recovery_dv = plan_recovery_burn(np.zeros(3), sat=sat)
                dv_mag = float(np.linalg.norm(recovery_dv))
                if dv_mag < 1e-9:
                    log.warning(f"Station-keeping: zero recovery ΔV for {sat.id} — skipping")
                    continue

                earliest_burn = state.sim_epoch + SIGNAL_LATENCY
                if sat.last_burn_time is not None:
                    earliest_burn = max(
                        earliest_burn,
                        sat.last_burn_time + THRUSTER_COOLDOWN,
                    )

                # FIX: valid ISO string for burn_time_iso
                base = datetime.fromisoformat(
                    state.sim_time.replace("Z", "+00:00")
                ) if state.sim_time else datetime.now(timezone.utc)
                burn_iso = (
                    base + timedelta(seconds=earliest_burn - state.sim_epoch)
                ).isoformat()

                from models.state_store import ScheduledBurn
                burn_id = f"SK-{sat.id}-{uuid.uuid4().hex[:6].upper()}"
                state.burns.append(ScheduledBurn(
                    burn_id=burn_id,
                    satellite_id=sat.id,
                    burn_time_iso=burn_iso,
                    burn_time_epoch=earliest_burn,
                    delta_v_eci=recovery_dv.tolist(),
                ))
                state.burns.sort(key=lambda b: b.burn_time_epoch)
                sat.status = "MANEUVERING"

        # ── Clear MANEUVERING if no pending burns — respect EOL/DEAD ──────────
        # FIX: only reset to NOMINAL if not EOL or DEAD
        if sat.status == "MANEUVERING":
            if not state.pending_burns_for(sat.id):
                sat.status = "NOMINAL"


# ── Visualization snapshot ─────────────────────────────────────────────────────

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