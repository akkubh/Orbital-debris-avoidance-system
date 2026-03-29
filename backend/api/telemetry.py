"""
api/telemetry.py
━━━━━━━━━━━━━━━━
Telemetry Ingestion & Auto-Evasion for ACM
"""

import uuid
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException

from models.schemas import (
    TelemetryRequest, TelemetryResponse, BurnCommand, Vec3,
)
from models.state_store import (
    state_store, SIGNAL_LATENCY, SAFE_MISS_KM, EOL_FUEL_FRAC, INITIAL_FUEL_KG,
    THRUSTER_COOLDOWN, save_state,
)
from physics.conjunction import screen_conjunctions
from physics.maneuver_calc import (
    plan_evasion_burn, plan_recovery_burn, optimize_delta_v,
    fuel_consumed,
)
from physics.ground_station import has_line_of_sight, next_los_window

logger = logging.getLogger("ACM.Telemetry")
router = APIRouter(tags=["Telemetry"])

AUTO_EVASION_RISK_LEVELS = {"HIGH", "CRITICAL"}


# ── Telemetry ingestion ───────────────────────────────────────────────────────

@router.post("/api/telemetry", response_model=TelemetryResponse)
async def ingest_telemetry(payload: TelemetryRequest):
    timestamp        = payload.timestamp
    sat_ids_ingested = []

    for obj in payload.objects:
        position = list(obj.r.to_list())
        velocity = list(obj.v.to_list())

        if obj.type == "SAT":
            state_store.update_satellite(
                satellite_id=obj.id,
                position=position,
                velocity=velocity,
                timestamp=timestamp,
            )
            sat_ids_ingested.append(obj.id)
            logger.info(f"[ACM] Telemetry ingested: SAT {obj.id}")
        elif obj.type == "DEBRIS":
            state_store.update_debris(
                debris_id=obj.id,
                position=position,
                velocity=velocity,
                timestamp=timestamp,
            )
            logger.debug(f"[ACM] Telemetry ingested: DEBRIS {obj.id}")

    conjunction_events = screen_conjunctions(
        satellites=state_store.satellites,
        debris_objects=state_store.debris,
    )

    _sync_cdm_warnings(conjunction_events)

    total_evasions = 0
    for sat_id in sat_ids_ingested:
        total_evasions += _auto_schedule_evasions(conjunction_events, sat_id)

    # FIX: save_state after conjunction screening + evasion scheduling so
    # CDM warnings and newly queued burns are persisted together in one write.
    save_state()

    # FIX: return actual stored unresolved warning count, not raw screen count
    # (these differ when evasion scheduling resolves some warnings mid-loop)
    return TelemetryResponse(
        status="ACK",
        processed_count=len(payload.objects),
        active_cdm_warnings=state_store.active_cdm_count(),
    )


# ── CDM warning sync ──────────────────────────────────────────────────────────

def _sync_cdm_warnings(events: list) -> None:
    active_pairs = set()
    for event in events:
        state_store.upsert_cdm_warning(
            sat_id=event.satellite_id,
            deb_id=event.debris_id,
            tca_epoch=state_store.sim_epoch + event.tca_seconds,
            miss_distance_km=event.miss_distance,
            risk_level=event.risk_level,
        )
        active_pairs.add((event.satellite_id, event.debris_id))

    for w in state_store.cdm_warnings:
        if not w.resolved and (w.sat_id, w.deb_id) not in active_pairs:
            w.resolved = True


# ── Auto-evasion logic ────────────────────────────────────────────────────────

def _auto_schedule_evasions(conjunction_events: list, triggering_sat_id: str) -> int:
    scheduled_count = 0

    for event in conjunction_events:
        if event.satellite_id != triggering_sat_id:
            continue

        risk_level = getattr(event, "risk_level", "LOW")
        risk_score = getattr(event, "risk_score",  0.0)

        if risk_level not in AUTO_EVASION_RISK_LEVELS:
            if risk_level == "MEDIUM":
                logger.warning(
                    f"[ACM] MEDIUM risk — monitoring: {event.satellite_id} vs "
                    f"{event.debris_id} | miss={event.miss_distance:.3f} km"
                )
            else:
                logger.info(
                    f"[ACM] LOW risk: {event.satellite_id} vs "
                    f"{event.debris_id} (score={risk_score:.4f})"
                )
            continue

        sat_state = state_store.satellites.get(event.satellite_id)
        deb_state = state_store.debris.get(event.debris_id)

        if sat_state is None or deb_state is None:
            logger.error(
                f"[ACM] Missing state for {event.satellite_id} or "
                f"{event.debris_id} — skipping"
            )
            continue

        if sat_state.status in ("DEAD", "EOL"):
            logger.warning(
                f"[ACM] Skipping evasion for {event.satellite_id} "
                f"— status={sat_state.status}"
            )
            continue

        evasion_cooldown_ok = True
        for b in state_store.burns:
            if b.satellite_id != event.satellite_id:
                continue
            if not b.burn_id.startswith("EVA-"):
                continue
            if not b.executed:
                evasion_cooldown_ok = False
                break
            time_since = state_store.sim_epoch - b.burn_time_epoch
            if 0 <= time_since < THRUSTER_COOLDOWN:
                evasion_cooldown_ok = False
                break

        if not evasion_cooldown_ok:
            logger.debug(
                f"[ACM] Evasion cooldown active for {event.satellite_id} "
                f"vs {event.debris_id} — skipping"
            )
            continue

        sat_pos  = np.asarray(list(sat_state.r), dtype=float)
        sat_vel  = np.asarray(list(sat_state.v), dtype=float)
        deb_pos  = np.asarray(list(deb_state.r), dtype=float)
        deb_vel  = np.asarray(list(deb_state.v), dtype=float)
        dry_mass = sat_state.dry_mass_kg

        logger.warning(
            f"[ACM] {risk_level} risk — scheduling evasion: "
            f"{event.satellite_id} vs {event.debris_id} | "
            f"miss={event.miss_distance:.4f} km | TCA={event.tca_seconds:.0f}s"
        )

        optimal_dv = optimize_delta_v(
            sat_pos, sat_vel, deb_pos, deb_vel,
            tca_seconds=event.tca_seconds,
            direction_name="TRANSVERSE",
        )
        if optimal_dv is not None:
            logger.info(
                f"[ACM] Optimal ΔV = {optimal_dv*1000:.1f} m/s "
                f"for {event.satellite_id}"
            )

        maneuver = plan_evasion_burn(
            sat_pos, sat_vel, deb_pos, deb_vel,
            tca_seconds=event.tca_seconds,
            dry_mass_kg=dry_mass,
        )
        if maneuver is None:
            logger.error(f"[ACM] Evasion planning failed for {event.satellite_id}")
            continue

        evasion_fuel_cost = maneuver["fuel_cost"]
        remaining_fuel    = sat_state.fuel_kg - evasion_fuel_cost
        eol_reserve       = EOL_FUEL_FRAC * INITIAL_FUEL_KG
        if remaining_fuel <= eol_reserve:
            logger.warning(
                f"[ACM] Insufficient fuel for recovery burn after evasion "
                f"for {event.satellite_id} — scheduling evasion only"
            )
            recovery_dv_arr = None
        else:
            recovery_dv_arr = plan_recovery_burn(maneuver["dv_eci"], sat=sat_state)

        sim_now_iso = state_store.sim_time or datetime.now(timezone.utc).isoformat()
        default_evasion_offset = max(event.tca_seconds * 0.5, SIGNAL_LATENCY)
        evasion_offset_s = _resolve_evasion_time(
            sat_state, event.tca_seconds, default_evasion_offset, sim_now_iso
        )

        evasion_time  = _iso_offset_from(sim_now_iso, evasion_offset_s)
        recovery_time = _iso_offset_from(
            sim_now_iso,
            max(event.tca_seconds * 1.5, evasion_offset_s + 60.0),
        )

        dv = maneuver["dv_eci"]
        evasion_cmd = BurnCommand(
            burn_id=f"EVA-{event.satellite_id}-{uuid.uuid4().hex[:6].upper()}",
            burnTime=evasion_time,
            deltaV_vector=Vec3(x=float(dv[0]), y=float(dv[1]), z=float(dv[2])),
        )
        state_store.add_maneuver(event.satellite_id, evasion_cmd)

        if recovery_dv_arr is not None:
            rdv = recovery_dv_arr
            recovery_cmd = BurnCommand(
                burn_id=f"REC-{event.satellite_id}-{uuid.uuid4().hex[:6].upper()}",
                burnTime=recovery_time,
                deltaV_vector=Vec3(x=float(rdv[0]), y=float(rdv[1]), z=float(rdv[2])),
            )
            state_store.add_maneuver(event.satellite_id, recovery_cmd)

        # FIX: removed resolve_cdm_warnings_for() call here.
        # Resolving immediately caused _sync_cdm_warnings on the next telemetry
        # call to re-open the warning (upsert sets resolved=False) since the
        # debris is still close, creating an infinite re-schedule loop that the
        # cooldown guard had to silently suppress. CDM warnings now stay active
        # until debris genuinely moves out of the screening threshold.

        logger.info(
            f"[ACM] Burns scheduled for {event.satellite_id} | "
            f"dir={maneuver['direction']} | "
            f"dv={maneuver['dv_kms']*1000:.1f} m/s | "
            f"fuel={maneuver['fuel_cost']:.4f} kg"
        )
        scheduled_count += 1

    return scheduled_count


# ── LOS / timing helpers ──────────────────────────────────────────────────────

def _resolve_evasion_time(
    sat_state, tca_seconds: float, default_offset_s: float, sim_now_iso: str
) -> float:
    from physics.propagator import rk4_step as _rk4

    current_los, _ = has_line_of_sight(list(sat_state.r), sim_now_iso)

    if not current_los:
        window = next_los_window(
            sat_r=list(sat_state.r),
            sat_v=list(sat_state.v),
            sim_time_iso=sim_now_iso,
            search_window_s=min(tca_seconds, 7200.0),
        )
        if window["found"]:
            upload_offset = window["offset_s"] + SIGNAL_LATENCY
            logger.info(
                f"[ACM] Blackout — LOS in {window['offset_s']:.0f}s, "
                f"evasion offset={upload_offset:.0f}s"
            )
            return max(upload_offset, SIGNAL_LATENCY)
        else:
            logger.warning(
                f"[ACM] No LOS before TCA={tca_seconds:.0f}s — "
                f"using default offset={default_offset_s:.0f}s"
            )
            return max(default_offset_s, SIGNAL_LATENCY)

    probe_state = np.array(list(sat_state.r) + list(sat_state.v), dtype=float)
    probe_dt    = 30.0
    elapsed     = 0.0
    blackout_at = None

    while elapsed < tca_seconds:
        step        = min(probe_dt, tca_seconds - elapsed)
        probe_state = _rk4(probe_state, step)
        elapsed    += step
        future_iso  = _iso_offset_from(sim_now_iso, elapsed)
        future_los, _ = has_line_of_sight(probe_state[:3].tolist(), future_iso)
        if not future_los:
            blackout_at = elapsed
            break

    if blackout_at is not None:
        safe_offset = max(blackout_at - SIGNAL_LATENCY, SIGNAL_LATENCY)
        logger.info(
            f"[ACM] LOS drops at T+{blackout_at:.0f}s — "
            f"scheduling evasion at T+{safe_offset:.0f}s"
        )
        return safe_offset

    return max(default_offset_s, SIGNAL_LATENCY)


def _iso_offset_from(base_iso: str, seconds: float) -> str:
    """Return ISO timestamp = base_iso + seconds."""
    try:
        base = datetime.fromisoformat(base_iso.replace("Z", "+00:00"))
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
    except Exception:
        base = datetime.now(timezone.utc)
    return (base + timedelta(seconds=seconds)).isoformat()