"""
POST /api/telemetry
Ingests orbital state vectors for satellites and debris.
Updates in-memory state store asynchronously.
"""
import logging
from fastapi import APIRouter
from datetime import datetime

from models.schemas import TelemetryRequest, TelemetryResponse
from models.state_store import state, SpaceObject, INITIAL_FUEL_KG, DRY_MASS_KG
from physics.conjunction import screen_conjunctions

router = APIRouter()
log = logging.getLogger("telemetry")


@router.post("/api/telemetry", response_model=TelemetryResponse)
async def receive_telemetry(payload: TelemetryRequest):
    # Update simulation time on first telemetry or if advancing
    if state.sim_time is None:
        state.sim_time = payload.timestamp
        log.info(f"Simulation initialized at {state.sim_time}")

    processed = 0
    for obj in payload.objects:
        existing = state.objects.get(obj.id)

        if existing:
            # Update position and velocity in-place
            existing.r = obj.r.to_list()
            existing.v = obj.v.to_list()
        else:
            # New object — create with defaults
            new_obj = SpaceObject(
                id   = obj.id,
                type = obj.type,
                r    = obj.r.to_list(),
                v    = obj.v.to_list(),
            )
            # Satellites get fuel + nominal slot (first known position)
            if obj.type == "SAT":
                new_obj.fuel_kg    = INITIAL_FUEL_KG
                new_obj.dry_mass_kg = DRY_MASS_KG
                new_obj.nominal_r  = obj.r.to_list()
                new_obj.nominal_v  = obj.v.to_list()
                log.info(f"New satellite registered: {obj.id}")
            state.objects[obj.id] = new_obj

        processed += 1

    # Run conjunction screening after every telemetry batch
    _refresh_cdm_warnings()

    log.debug(f"Telemetry: {processed} objects, {state.active_cdm_count()} CDM warnings")
    
    from models.state_store import save_state
    save_state()

    return TelemetryResponse(
        status              = "ACK",
        processed_count     = processed,
        active_cdm_warnings = state.active_cdm_count(),
    )
    
    


def _refresh_cdm_warnings():
    """Re-run conjunction screening and update CDM list."""
    sats   = state.get_satellites()
    debris = state.get_debris()

    if not sats or not debris:
        return

    from physics.conjunction import screen_conjunctions
    events = screen_conjunctions(sats, debris)

    # Clear old unresolved warnings, add fresh ones
    state.cdm_warnings = [w for w in state.cdm_warnings if w.resolved]

    for event in events:
        from models.state_store import CDMWarning
        state.cdm_warnings.append(CDMWarning(
            sat_id           = event.sat_id,
            deb_id           = event.deb_id,
            tca_epoch        = state.sim_epoch + event.tca_offset_s,
            miss_distance_km = event.miss_distance_km,
        ))
        log.warning(
            f"CDM: {event.sat_id} ↔ {event.deb_id} | "
            f"TCA in {event.tca_offset_s:.0f}s | "
            f"miss {event.miss_distance_km*1000:.1f}m"
        )

    # Auto-schedule evasion for critical conjunctions
    _auto_schedule_evasions(events)


def _auto_schedule_evasions(events):
    """Automatically plan and queue evasion burns for unresolved conjunctions."""
    from physics.maneuver_calc import plan_evasion_burn, plan_recovery_burn
    from models.state_store import ScheduledBurn

    for event in events:
        sat = state.objects.get(event.sat_id)
        if not sat or sat.type != "SAT":
            continue

        # Don't re-plan if already scheduled
        already_planned = any(
            b.satellite_id == event.sat_id and not b.executed
            for b in state.burns
        )
        if already_planned:
            continue

        evasion = plan_evasion_burn(sat, event.tca_offset_s,
                                    event.miss_distance_km, state.sim_epoch)
        if not evasion:
            log.error(f"Could not plan evasion for {event.sat_id}")
            continue

        # Queue evasion burn
        state.burns.append(ScheduledBurn(
            burn_id        = evasion["burn_id"],
            satellite_id   = event.sat_id,
            burn_time_iso  = _epoch_to_iso(evasion["burn_epoch"]),
            burn_time_epoch = evasion["burn_epoch"],
            delta_v_eci    = evasion["delta_v_eci"],
        ))

        # Queue recovery burn
        if sat.nominal_r:
            recovery = plan_recovery_burn(sat, sat.nominal_r, sat.nominal_v,
                                          evasion["burn_epoch"], state.sim_epoch)
            if recovery:
                state.burns.append(ScheduledBurn(
                    burn_id         = recovery["burn_id"],
                    satellite_id    = event.sat_id,
                    burn_time_iso   = _epoch_to_iso(recovery["burn_epoch"]),
                    burn_time_epoch = recovery["burn_epoch"],
                    delta_v_eci     = recovery["delta_v_eci"],
                ))
                log.info(f"Recovery burn scheduled for {event.sat_id} at epoch {recovery['burn_epoch']:.0f}")

        log.info(f"Evasion burn scheduled for {event.sat_id} | ΔV={evasion['dv_magnitude']*1000:.2f} m/s")


def _epoch_to_iso(epoch: float) -> str:
    """Convert sim epoch offset to ISO string (approximate)."""
    from datetime import datetime, timezone, timedelta
    if state.sim_time:
        base = datetime.fromisoformat(state.sim_time.replace("Z", "+00:00"))
        result = base + timedelta(seconds=epoch - state.sim_epoch)
        return result.isoformat()
    return datetime.now(timezone.utc).isoformat()


