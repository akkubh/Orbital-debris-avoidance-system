"""
api/maneuver.py
━━━━━━━━━━━━━━━
POST /api/maneuver/schedule — validate and queue a maneuver burn sequence.

FIXES APPLIED:
  - has_line_of_sight uses sim_time (sim clock) not burn_cmd.burnTime
    for GMST calculation — consistent with telemetry.py's LOS checks
  - _iso_to_epoch gracefully handles None sim_time
  - Constants imported from state_store (no local copies)
"""

import logging
import numpy as np
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone

from models.schemas import ManeuverRequest, ManeuverResponse, ManeuverValidation
from models.state_store import (
    state, ScheduledBurn,
    INITIAL_FUEL_KG, EOL_FUEL_FRAC, SIGNAL_LATENCY,
    save_state,
)
from physics.maneuver_calc import validate_burn, fuel_consumed
from physics.ground_station import has_line_of_sight

router = APIRouter()
log    = logging.getLogger("maneuver")


def _iso_to_epoch(iso_str: str) -> float:
    """
    Convert ISO timestamp → sim_epoch offset.
    FIX: returns SIGNAL_LATENCY (not 0.0) when sim_time is None,
    so the burn isn't immediately rejected as "too early".
    """
    if state.sim_time is None:
        return state.sim_epoch + SIGNAL_LATENCY
    try:
        base    = datetime.fromisoformat(state.sim_time.replace("Z", "+00:00"))
        burn_dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if burn_dt.tzinfo is None:
            burn_dt = burn_dt.replace(tzinfo=timezone.utc)
        return state.sim_epoch + (burn_dt - base).total_seconds()
    except Exception:
        return state.sim_epoch + SIGNAL_LATENCY


@router.post("/api/maneuver/schedule", response_model=ManeuverResponse)
async def schedule_maneuver(payload: ManeuverRequest):
    sat_id = payload.satelliteId
    sat    = state.objects.get(sat_id)

    if not sat:
        raise HTTPException(status_code=404, detail=f"Satellite {sat_id} not found")
    if sat.type != "SAT":
        raise HTTPException(status_code=400, detail=f"{sat_id} is not a satellite")
    if sat.status == "DEAD":
        raise HTTPException(status_code=409, detail=f"{sat_id} is DEAD — no maneuvers possible")

    projected_mass = sat.wet_mass
    temp_last_burn = sat.last_burn_time
    los_ok         = True
    all_valid      = True
    reject_reason  = ""

    # FIX: use sim_time for LOS GMST — consistent with telemetry.py
    sim_now_iso = state.sim_time or datetime.now(timezone.utc).isoformat()

    for burn_cmd in payload.maneuver_sequence:
        burn_epoch = _iso_to_epoch(burn_cmd.burnTime)
        dv_eci     = burn_cmd.deltaV_vector.to_list()

        # Signal latency check
        if burn_epoch < state.sim_epoch + SIGNAL_LATENCY:
            reject_reason = (
                f"Burn epoch {burn_epoch:.1f}s is too early — must be at least "
                f"{SIGNAL_LATENCY:.0f}s after sim epoch {state.sim_epoch:.1f}s"
            )
            log.warning(f"[LATENCY] {sat_id}: {reject_reason}")
            return ManeuverResponse(
                status="REJECTED",
                validation=ManeuverValidation(
                    ground_station_los=los_ok,
                    sufficient_fuel=False,
                    projected_mass_remaining_kg=0.0,
                ),
            )

        # FIX: LOS check uses sim_now_iso (sim clock), not burn_cmd.burnTime
        # burn_cmd.burnTime is a future wall-clock time; for GMST we want
        # the current simulation epoch's Earth orientation
        burn_los, visible_stations = has_line_of_sight(sat.r, sim_now_iso)
        if not burn_los:
            los_ok        = False
            reject_reason = (
                f"No ground-station LOS for {sat_id} at sim time {sim_now_iso}"
            )
            log.warning(f"[LOS] {reject_reason}")
            return ManeuverResponse(
                status="REJECTED",
                validation=ManeuverValidation(
                    ground_station_los=False,
                    sufficient_fuel=False,
                    projected_mass_remaining_kg=0.0,
                ),
            )

        # Physics / thruster validation via TempSat
        class TempSat:
            def __init__(self_):
                self_.wet_mass       = projected_mass
                self_.fuel_kg        = max(0.0, projected_mass - sat.dry_mass_kg)
                self_.dry_mass_kg    = sat.dry_mass_kg
                self_.last_burn_time = temp_last_burn
                self_.r              = sat.r
                self_.v              = sat.v

        ok, reason, new_mass = validate_burn(dv_eci, TempSat(), state.sim_epoch, burn_epoch)
        if not ok:
            all_valid     = False
            reject_reason = reason
            break

        projected_mass = new_mass
        temp_last_burn = burn_epoch

    if not all_valid:
        return ManeuverResponse(
            status="REJECTED",
            validation=ManeuverValidation(
                ground_station_los=los_ok,
                sufficient_fuel=False,
                projected_mass_remaining_kg=0.0,
            ),
        )

    # All burns valid — queue them
    for burn_cmd in payload.maneuver_sequence:
        burn_epoch = _iso_to_epoch(burn_cmd.burnTime)
        dv_eci     = burn_cmd.deltaV_vector.to_list()

        state.burns.append(ScheduledBurn(
            burn_id=burn_cmd.burn_id,
            satellite_id=sat_id,
            burn_time_iso=burn_cmd.burnTime,
            burn_time_epoch=burn_epoch,
            delta_v_eci=dv_eci,
        ))

    state.burns.sort(key=lambda b: b.burn_time_epoch)
    save_state()

    log.info(
        f"Maneuver scheduled for {sat_id}: "
        f"{len(payload.maneuver_sequence)} burns, "
        f"projected mass {projected_mass:.2f} kg"
    )

    return ManeuverResponse(
        status="SCHEDULED",
        validation=ManeuverValidation(
            ground_station_los=los_ok,
            sufficient_fuel=True,
            projected_mass_remaining_kg=round(projected_mass, 2),
        ),
    )