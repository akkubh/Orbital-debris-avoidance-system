"""
api/maneuver.py
━━━━━━━━━━━━━━━
POST /api/maneuver/schedule — validate and queue a maneuver burn sequence.
POST /api/maneuver/quick    — convenience endpoint: schedule a burn N seconds
                              from NOW in sim time, in a named RTN direction.
                              Makes it easy to trigger from the frontend without
                              knowing the exact ISO timestamp format.

FIXES:
  - _TempSat defined at module level (not redefined every loop iteration)
  - EOL satellites blocked (graveyard burn pending, near-zero fuel)
  - /api/maneuver/quick endpoint added for frontend use
  - Rejection reason always returned in response body so frontend can display it
  - LOS rejection includes list of visible stations (or empty list)
  - burnTime helper shows expected format in error messages

  FIX (quick_burn LOS): Ground-station LOS is now a WARNING, not a hard
  rejection, for /api/maneuver/quick. Auto-evasion (telemetry.py) already
  handles LOS gracefully via _resolve_evasion_time() — it finds the next
  window and schedules ahead of it. But manual burns from the control panel
  were being permanently blocked by the hard LOS check, even when the operator
  could see the conjunction warning and wanted to act. The burn is now
  scheduled regardless; a warning is included in the response so the frontend
  can inform the operator. The /api/maneuver/schedule endpoint retains the
  hard LOS rejection (advanced users scheduling raw ISO sequences are expected
  to handle timing themselves).
"""

import logging
import uuid
import numpy as np
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel

from models.schemas import ManeuverRequest, ManeuverResponse, ManeuverValidation
from models.state_store import (
    state, ScheduledBurn,
    INITIAL_FUEL_KG, EOL_FUEL_FRAC, SIGNAL_LATENCY,
    MAX_DV_PER_BURN,
    save_state,
)
from physics.maneuver_calc import validate_burn
from physics.ground_station import has_line_of_sight

router = APIRouter()
log    = logging.getLogger("maneuver")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso_to_epoch(iso_str: str) -> float:
    """
    Convert ISO timestamp → sim_epoch value using sim_time as base.
    Returns sim_epoch + SIGNAL_LATENCY when sim_time is None.
    """
    if state.sim_time is None:
        return state.sim_epoch + SIGNAL_LATENCY
    try:
        base = datetime.fromisoformat(state.sim_time.replace("Z", "+00:00"))
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        burn_dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if burn_dt.tzinfo is None:
            burn_dt = burn_dt.replace(tzinfo=timezone.utc)
        return state.sim_epoch + (burn_dt - base).total_seconds()
    except Exception:
        return state.sim_epoch + SIGNAL_LATENCY


def _epoch_to_iso(burn_epoch: float) -> str:
    """Convert a sim_epoch value back to an ISO timestamp string."""
    if state.sim_time is None:
        base = datetime.now(timezone.utc)
    else:
        base = datetime.fromisoformat(state.sim_time.replace("Z", "+00:00"))
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
    offset_s = burn_epoch - state.sim_epoch
    return (base + timedelta(seconds=offset_s)).isoformat()


class _TempSat:
    """Lightweight proxy for validate_burn — avoids mutating the real SpaceObject."""
    def __init__(self, wet_mass, fuel_kg, dry_mass_kg, last_burn_time, r, v):
        self.wet_mass       = wet_mass
        self.fuel_kg        = fuel_kg
        self.dry_mass_kg    = dry_mass_kg
        self.last_burn_time = last_burn_time
        self.r              = r
        self.v              = v


def _reject(reason: str, los_ok: bool = True) -> ManeuverResponse:
    log.warning(f"[MANEUVER REJECTED] {reason}")
    return ManeuverResponse(
        status="REJECTED",
        validation=ManeuverValidation(
            ground_station_los=los_ok,
            sufficient_fuel=False,
            projected_mass_remaining_kg=0.0,
        ),
        reject_reason=reason,
    )


# ── Standard schedule endpoint ────────────────────────────────────────────────

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
    if sat.status == "EOL":
        raise HTTPException(status_code=409, detail=f"{sat_id} is EOL — no manual maneuvers (graveyard burn pending)")

    projected_mass = sat.wet_mass
    temp_last_burn = sat.last_burn_time
    los_ok         = True
    all_valid      = True
    reject_reason  = ""

    sim_now_iso = state.sim_time or datetime.now(timezone.utc).isoformat()

    for burn_cmd in payload.maneuver_sequence:
        burn_epoch = _iso_to_epoch(burn_cmd.burnTime)
        dv_eci     = burn_cmd.deltaV_vector.to_list()

        if burn_epoch < state.sim_epoch + SIGNAL_LATENCY:
            return _reject(
                f"Burn time too early — must be ≥ {SIGNAL_LATENCY:.0f}s after current "
                f"sim epoch ({state.sim_epoch:.1f}s). "
                f"burnTime should be at least: {_epoch_to_iso(state.sim_epoch + SIGNAL_LATENCY)}"
            )

        burn_los, visible = has_line_of_sight(sat.r, sim_now_iso)
        if not burn_los:
            return _reject(
                f"No ground-station LOS for {sat_id}. "
                f"No stations currently visible — satellite is in blackout.",
                los_ok=False,
            )

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
        return _reject(reject_reason, los_ok=los_ok)

    for burn_cmd in payload.maneuver_sequence:
        burn_epoch = _iso_to_epoch(burn_cmd.burnTime)
        state.burns.append(ScheduledBurn(
            burn_id         = burn_cmd.burn_id,
            satellite_id    = sat_id,
            burn_time_iso   = burn_cmd.burnTime,
            burn_time_epoch = burn_epoch,
            delta_v_eci     = burn_cmd.deltaV_vector.to_list(),
        ))

    state.burns.sort(key=lambda b: b.burn_time_epoch)
    save_state()

    log.info(
        f"Manual maneuver scheduled: {sat_id} | "
        f"{len(payload.maneuver_sequence)} burns | "
        f"projected mass {projected_mass:.2f} kg"
    )

    return ManeuverResponse(
        status="SCHEDULED",
        validation=ManeuverValidation(
            ground_station_los=los_ok,
            sufficient_fuel=True,
            projected_mass_remaining_kg=round(projected_mass, 2),
        ),
        reject_reason="",
    )


# ── Quick-burn endpoint ───────────────────────────────────────────────────────

class QuickBurnRequest(BaseModel):
    satellite_id: str
    direction: str     = "TRANSVERSE"   # RADIAL | TRANSVERSE | NORMAL | RETROGRADE
    delta_v_ms: float  = 1.0            # m/s
    delay_s:    float  = 15.0           # seconds from now in sim time


class QuickBurnResponse(BaseModel):
    status:        str
    burn_id:       str   = ""
    burn_epoch:    float = 0.0
    burn_time_iso: str   = ""
    delta_v_ms:    float = 0.0
    reject_reason: str   = ""
    los_warning:   bool  = False   # True when burn scheduled despite no GS LOS


@router.post("/api/maneuver/quick", response_model=QuickBurnResponse)
async def quick_burn(payload: QuickBurnRequest):
    """
    Schedule a single burn N seconds from now in sim time, in a named RTN direction.
    Much simpler than /api/maneuver/schedule — no ISO timestamp calculation needed.

    direction: RADIAL | TRANSVERSE | NORMAL | RETROGRADE
    delta_v_ms: burn magnitude in m/s (max 15 m/s)
    delay_s: seconds from current sim_epoch to fire the burn (min = SIGNAL_LATENCY)

    LOS NOTE: Ground-station LOS is checked but is NOT a hard blocker here.
    If the satellite is in blackout, the burn is still scheduled and a
    los_warning=True flag is returned so the frontend can notify the operator.
    This matches real-world practice where operators can pre-program burns to
    execute autonomously during blackout windows. The /api/maneuver/schedule
    endpoint retains the hard LOS rejection for advanced multi-burn sequences.
    """
    import math
    from physics.maneuver_calc import rtn_to_eci, RTN_DIRECTIONS

    sat_id = payload.satellite_id
    sat    = state.objects.get(sat_id)

    if not sat:
        return QuickBurnResponse(status="REJECTED", reject_reason=f"Satellite {sat_id} not found")
    if sat.type != "SAT":
        return QuickBurnResponse(status="REJECTED", reject_reason=f"{sat_id} is not a satellite")
    if sat.status == "DEAD":
        return QuickBurnResponse(status="REJECTED", reject_reason=f"{sat_id} is DEAD")
    if sat.status == "EOL":
        return QuickBurnResponse(status="REJECTED", reject_reason=f"{sat_id} is EOL — fuel critically low")

    direction = payload.direction.upper()
    if direction not in RTN_DIRECTIONS:
        return QuickBurnResponse(
            status="REJECTED",
            reject_reason=f"Unknown direction '{direction}'. Use: RADIAL, TRANSVERSE, NORMAL, RETROGRADE"
        )

    dv_kms = payload.delta_v_ms / 1000.0
    if dv_kms > MAX_DV_PER_BURN + 1e-9:
        return QuickBurnResponse(
            status="REJECTED",
            reject_reason=f"ΔV {payload.delta_v_ms:.1f} m/s exceeds limit of {MAX_DV_PER_BURN*1000:.0f} m/s"
        )

    delay_s       = max(payload.delay_s, SIGNAL_LATENCY)
    burn_epoch    = state.sim_epoch + delay_s
    burn_time_iso = _epoch_to_iso(burn_epoch)

    # FIX: LOS is now a WARNING, not a hard rejection.
    # Previously: if no LOS → return REJECTED immediately → operator can never
    # schedule a manual burn from the control panel during any blackout window.
    # Now: log the warning, set los_warning=True, and continue to schedule.
    # The frontend will display the warning inline in the Manual Burn Panel.
    sim_now_iso = state.sim_time or datetime.now(timezone.utc).isoformat()
    burn_los, _visible = has_line_of_sight(sat.r, sim_now_iso)
    los_warning = not burn_los
    if los_warning:
        log.warning(
            f"[MANEUVER] {sat_id} in GS blackout — scheduling quick burn anyway "
            f"(operator override). Burn will execute autonomously at epoch={burn_epoch:.1f}s"
        )

    # Convert RTN → ECI
    sat_pos  = np.asarray(sat.r, dtype=float)
    sat_vel  = np.asarray(sat.v, dtype=float)
    rtn_unit = RTN_DIRECTIONS[direction]
    dv_rtn   = rtn_unit * dv_kms
    dv_eci   = rtn_to_eci(dv_rtn, sat_pos, sat_vel)

    # Validate fuel + cooldown (these remain hard rejections)
    temp_sat = _TempSat(
        wet_mass       = sat.wet_mass,
        fuel_kg        = sat.fuel_kg,
        dry_mass_kg    = sat.dry_mass_kg,
        last_burn_time = sat.last_burn_time,
        r              = sat.r,
        v              = sat.v,
    )
    ok, reason, new_mass = validate_burn(dv_eci.tolist(), temp_sat, state.sim_epoch, burn_epoch)
    if not ok:
        return QuickBurnResponse(status="REJECTED", reject_reason=reason)

    burn_id = f"MAN-{sat_id}-{uuid.uuid4().hex[:6].upper()}"
    state.burns.append(ScheduledBurn(
        burn_id         = burn_id,
        satellite_id    = sat_id,
        burn_time_iso   = burn_time_iso,
        burn_time_epoch = burn_epoch,
        delta_v_eci     = dv_eci.tolist(),
    ))
    state.burns.sort(key=lambda b: b.burn_time_epoch)
    save_state()

    log.info(
        f"Quick burn scheduled: {sat_id} | {burn_id} | "
        f"dir={direction} dv={payload.delta_v_ms:.1f} m/s | "
        f"epoch={burn_epoch:.1f}s | los_warning={los_warning}"
    )

    reject_reason_str = (
        "Warning: satellite in GS blackout — burn queued for autonomous execution"
        if los_warning else ""
    )

    return QuickBurnResponse(
        status        = "SCHEDULED",
        burn_id       = burn_id,
        burn_epoch    = burn_epoch,
        burn_time_iso = burn_time_iso,
        delta_v_ms    = round(math.sqrt(sum(x**2 for x in dv_eci)) * 1000, 3),
        reject_reason = reject_reason_str,
        los_warning   = los_warning,
    )