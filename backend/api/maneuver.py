"""
api/maneuver.py
━━━━━━━━━━━━━━━
POST /api/maneuver/schedule — validate and queue a maneuver burn sequence.
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
from physics.maneuver_calc import validate_burn
from physics.ground_station import has_line_of_sight

router = APIRouter()
log    = logging.getLogger("maneuver")


def _iso_to_epoch(iso_str: str) -> float:
    """
    Convert ISO timestamp → sim_epoch offset.
    Returns SIGNAL_LATENCY offset when sim_time is None so the burn
    isn't immediately rejected as "too early".
    """
    if state.sim_time is None:
        return state.sim_epoch + SIGNAL_LATENCY
    try:
        base    = datetime.fromisoformat(state.sim_time.replace("Z", "+00:00"))
        # FIX: ensure base is timezone-aware so subtraction with aware burn_dt
        # doesn't raise TypeError on Python <3.11 where fromisoformat may
        # return a naive datetime even with +00:00 in some edge cases.
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        burn_dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if burn_dt.tzinfo is None:
            burn_dt = burn_dt.replace(tzinfo=timezone.utc)
        return state.sim_epoch + (burn_dt - base).total_seconds()
    except Exception:
        return state.sim_epoch + SIGNAL_LATENCY


# FIX: TempSat defined once at module level outside any loop.
# Previously defined inside the for-burn loop — redefined every iteration.
class _TempSat:
    """Lightweight proxy used by validate_burn for sequential burn planning."""
    def __init__(self, wet_mass, fuel_kg, dry_mass_kg, last_burn_time, r, v):
        self.wet_mass       = wet_mass
        self.fuel_kg        = fuel_kg
        self.dry_mass_kg    = dry_mass_kg
        self.last_burn_time = last_burn_time
        self.r              = r
        self.v              = v


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
    # FIX: also block EOL satellites — they have a graveyard burn pending and
    # near-zero fuel. Manual burns would interfere with deorbit sequence or
    # fail fuel validation, leaving the sequence in a broken state.
    if sat.status == "EOL":
        raise HTTPException(status_code=409, detail=f"{sat_id} is EOL — no manual maneuvers possible")

    projected_mass = sat.wet_mass
    temp_last_burn = sat.last_burn_time
    los_ok         = True
    all_valid      = True
    reject_reason  = ""

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

        # LOS check uses sim_now_iso (sim clock) for consistent GMST
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

        # Physics / thruster validation
        temp_sat = _TempSat(
            wet_mass       = projected_mass,
            fuel_kg        = max(0.0, projected_mass - sat.dry_mass_kg),
            dry_mass_kg    = sat.dry_mass_kg,
            last_burn_time = temp_last_burn,
            r              = sat.r,
            v              = sat.v,
        )
        ok, reason, new_mass = validate_burn(dv_eci, temp_sat, state.sim_epoch, burn_epoch)
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