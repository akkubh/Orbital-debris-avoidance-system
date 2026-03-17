"""
POST /api/simulate/step  — advance simulation time
GET  /api/visualization/snapshot — current state snapshot for frontend
"""
import logging
import numpy as np
from fastapi import APIRouter
from datetime import datetime, timezone, timedelta

from models.schemas import SimStepRequest, SimStepResponse, VisualizationSnapshot, SatelliteSnapshot
from models.state_store import state, INITIAL_FUEL_KG, EOL_FUEL_FRAC, STATION_BOX_KM
from physics.propagator import rk4_step, eci_to_geodetic, compute_gmst
from physics.maneuver_calc import fuel_consumed, plan_graveyard_burn
from physics.conjunction import check_current_collisions

router = APIRouter()
log = logging.getLogger("simulate")

PROPAGATION_DT = 10.0   # seconds per RK4 step


@router.post("/api/simulate/step", response_model=SimStepResponse)
async def simulate_step(payload: SimStepRequest):
    if state.sim_time is None:
        # Bootstrap with current time if no telemetry received yet
        state.sim_time = datetime.now(timezone.utc).isoformat()

    step_s            = payload.step_seconds
    maneuvers_executed = 0
    collisions_found   = 0

    # Propagate in small sub-steps, executing burns when due
    elapsed      = 0.0
    sub_dt       = PROPAGATION_DT

    while elapsed < step_s:
        dt       = min(sub_dt, step_s - elapsed)
        target_e = state.sim_epoch + dt

        # Execute any burns scheduled in this sub-step window
        for burn in state.burns:
            if burn.executed:
                continue
            if burn.burn_time_epoch <= target_e:
                sat = state.objects.get(burn.satellite_id)
                if sat and sat.type == "SAT" and sat.status != "DEAD":
                    _execute_burn(sat, burn)
                    maneuvers_executed += 1

        # Propagate ALL objects one sub-step
        for obj in state.objects.values():
            s = np.array(obj.r + obj.v, dtype=float)
            s = rk4_step(s, dt)
            obj.r = s[:3].tolist()
            obj.v = s[3:].tolist()

        state.sim_epoch += dt
        elapsed         += dt

    # Advance sim_time string
    base_dt = datetime.fromisoformat(state.sim_time.replace("Z", "+00:00"))
    new_dt  = base_dt + timedelta(seconds=step_s)
    state.sim_time = new_dt.isoformat()

    # Post-step collision check
    sats   = state.get_satellites()
    debris = state.get_debris()
    hits   = check_current_collisions(sats, debris)
    for hit in hits:
        collisions_found += 1
        state.total_collisions += 1
        sat = state.objects.get(hit["sat_id"])
        if sat:
            sat.status = "DEAD"
        log.error(
            f"COLLISION: {hit['sat_id']} ↔ {hit['deb_id']} | "
            f"distance {hit['distance_km']*1000:.1f}m"
        )

    # Update satellite statuses and check EOL
    _update_satellite_statuses()

    state.total_maneuvers_executed += maneuvers_executed

    log.info(
        f"Tick +{step_s:.0f}s → {state.sim_time} | "
        f"maneuvers={maneuvers_executed} collisions={collisions_found}"
    )

    from models.state_store import save_state
    save_state()
    
    return SimStepResponse(
        status               = "STEP_COMPLETE",
        new_timestamp        = state.sim_time,
        collisions_detected  = collisions_found,
        maneuvers_executed   = maneuvers_executed,
    )


def _execute_burn(sat, burn):
    """Apply ΔV to satellite and deduct fuel mass."""
    dv_eci  = np.array(burn.delta_v_eci, dtype=float)
    dv_mag  = float(np.linalg.norm(dv_eci))

    dm = fuel_consumed(dv_mag, sat.wet_mass)
    sat.fuel_kg        = max(0.0, sat.fuel_kg - dm)
    sat.v              = (np.array(sat.v) + dv_eci).tolist()
    sat.last_burn_time = burn.burn_time_epoch
    sat.status         = "MANEUVERING"
    burn.executed      = True

    log.info(
        f"Burn executed: {burn.burn_id} on {sat.id} | "
        f"ΔV={dv_mag*1000:.3f} m/s | fuel remaining={sat.fuel_kg:.2f} kg"
    )


def _update_satellite_statuses():
    """Check station-keeping, EOL, and reset MANEUVERING status."""
    for sat in state.get_satellites():
        if sat.status == "DEAD":
            continue

        # EOL check — auto-schedule graveyard burn
        if sat.fuel_fraction < EOL_FUEL_FRAC and sat.status != "EOL":
            sat.status = "EOL"
            graveyard = plan_graveyard_burn(sat, state.sim_epoch)
            if graveyard:
                from models.state_store import ScheduledBurn
                state.burns.append(ScheduledBurn(
                    burn_id         = graveyard["burn_id"],
                    satellite_id    = sat.id,
                    burn_time_iso   = "",
                    burn_time_epoch = graveyard["burn_epoch"],
                    delta_v_eci     = graveyard["delta_v_eci"],
                ))
                log.warning(f"EOL graveyard burn scheduled for {sat.id}")

        # Station-keeping box check
        if sat.nominal_r and sat.status not in ("MANEUVERING", "EOL", "DEAD"):
            dist_from_slot = float(np.linalg.norm(
                np.array(sat.r) - np.array(sat.nominal_r)
            ))
            if dist_from_slot <= STATION_BOX_KM:
                sat.status = "NOMINAL"

        # Clear MANEUVERING if no pending burns
        if sat.status == "MANEUVERING":
            pending = state.pending_burns_for(sat.id)
            if not pending:
                sat.status = "NOMINAL"


# ── Visualization snapshot ────────────────────────────────────────────

@router.get("/api/visualization/snapshot", response_model=VisualizationSnapshot)
async def visualization_snapshot():
    gmst = compute_gmst(state.sim_time) if state.sim_time else 0.0

    satellites    = []
    debris_cloud  = []

    for obj in state.objects.values():
        lat, lon, alt = eci_to_geodetic(obj.r, gmst)

        if obj.type == "SAT":
            satellites.append(SatelliteSnapshot(
                id      = obj.id,
                lat     = round(lat, 4),
                lon     = round(lon, 4),
                fuel_kg = round(obj.fuel_kg, 2),
                status  = obj.status,
            ))
        else:
            # Compact tuple format: [id, lat, lon, alt]
            debris_cloud.append([obj.id, round(lat, 2), round(lon, 2), round(alt, 1)])

    return VisualizationSnapshot(
        timestamp    = state.sim_time or "",
        satellites   = satellites,
        debris_cloud = debris_cloud,
    )
