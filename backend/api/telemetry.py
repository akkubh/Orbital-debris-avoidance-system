"""
api/telemetry.py
━━━━━━━━━━━━━━━━
Telemetry Ingestion & Auto-Evasion for ACM (Autonomous Constellation Manager)
National Space Hackathon 2026

FIX 6: _auto_schedule_evasions() now checks whether the satellite will lose
        LOS before TCA. If so, it calls next_los_window() to determine when
        LOS returns and schedules the maneuver before the upcoming blackout
        begins, while still respecting the SIGNAL_LATENCY rule.
"""

import uuid
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException

from models.schemas import (
    TelemetryRequest,
    TelemetryResponse,
    BurnCommand,
    Vec3,
)
from models.state_store import state_store, SIGNAL_LATENCY
from physics.conjunction import screen_conjunctions
from physics.maneuver_calc import plan_evasion_burn, plan_recovery_burn, optimize_delta_v
from physics.ground_station import has_line_of_sight, next_los_window

logger = logging.getLogger("ACM.Telemetry")

router = APIRouter(tags=["Telemetry"])

AUTO_EVASION_RISK_LEVELS = {"HIGH", "CRITICAL"}


# ── Telemetry Ingestion Endpoint ──────────────────────────────────────────────

@router.post("/api/telemetry", response_model=TelemetryResponse)
async def ingest_telemetry(payload: TelemetryRequest):
    timestamp        = payload.timestamp
    sat_ids_ingested = []

    for obj in payload.objects:
        position = obj.r.to_list()
        velocity = obj.v.to_list()

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

    total_evasions = 0
    for sat_id in sat_ids_ingested:
        total_evasions += _auto_schedule_evasions(conjunction_events, sat_id)

    return TelemetryResponse(
        status="ACK",
        processed_count=len(payload.objects),
        active_cdm_warnings=len(conjunction_events),
    )


# ── Auto-Evasion Logic ────────────────────────────────────────────────────────

def _auto_schedule_evasions(conjunction_events: list, triggering_sat_id: str) -> int:
    """
    Schedule evasion burns only for HIGH / CRITICAL risk events.

    FIX 6: Before scheduling, check if the satellite will lose LOS before TCA.
           If a blackout is coming, call next_los_window() and schedule the
           maneuver before the blackout window begins (still respecting
           SIGNAL_LATENCY).
    """
    scheduled_count = 0

    for event in conjunction_events:
        if event.satellite_id != triggering_sat_id:
            continue

        risk_level = getattr(event, "risk_level", "LOW")
        risk_score = getattr(event, "risk_score",  0.0)

        if risk_level == "LOW":
            logger.info(
                f"[ACM] Skipping LOW risk: {event.satellite_id} vs "
                f"{event.debris_id} (score={risk_score:.4f})"
            )
            continue

        if risk_level == "MEDIUM":
            logger.warning(
                f"[ACM] MEDIUM risk — monitoring: {event.satellite_id} vs "
                f"{event.debris_id} | miss={event.miss_distance:.3f} km"
            )
            continue

        # HIGH or CRITICAL
        logger.warning(
            f"[ACM] {risk_level} risk — scheduling evasion: "
            f"{event.satellite_id} vs {event.debris_id} | "
            f"miss={event.miss_distance:.3f} km | TCA={event.tca_seconds:.0f}s"
        )

        sat_state = state_store.satellites.get(event.satellite_id)
        deb_state = state_store.debris.get(event.debris_id)

        if sat_state is None or deb_state is None:
            logger.error(
                f"[ACM] Missing state for {event.satellite_id} or "
                f"{event.debris_id} — skipping"
            )
            continue

        sat_pos  = np.asarray(sat_state.r)
        sat_vel  = np.asarray(sat_state.v)
        deb_pos  = np.asarray(deb_state.r)
        deb_vel  = np.asarray(deb_state.v)
        dry_mass = getattr(sat_state, "dry_mass_kg", 500.0)

        if risk_level == "CRITICAL":
            optimal_dv = optimize_delta_v(
                sat_pos, sat_vel, deb_pos, deb_vel,
                tca_seconds=event.tca_seconds,
                direction_name="TRANSVERSE",
            )
            if optimal_dv is not None:
                logger.info(
                    f"[ACM] Optimal ΔV = {optimal_dv * 1000:.1f} m/s "
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

        recovery_dv_arr = plan_recovery_burn(maneuver["dv_eci"], sat=sat_state)

        # ── FIX 6: Blackout-aware burn timing ────────────────────────────────
        # Default evasion time: halfway to TCA
        default_evasion_offset = event.tca_seconds * 0.5
        evasion_offset_s       = _resolve_evasion_time(
            sat_state, event.tca_seconds, default_evasion_offset
        )

        evasion_time  = _iso_offset(evasion_offset_s)
        recovery_time = _iso_offset(event.tca_seconds * 1.5)

        dv  = maneuver["dv_eci"]
        rdv = recovery_dv_arr

        evasion_cmd = BurnCommand(
            burn_id=f"EVA-{event.satellite_id}-{uuid.uuid4().hex[:6].upper()}",
            burnTime=evasion_time,
            deltaV_vector=Vec3(x=float(dv[0]), y=float(dv[1]), z=float(dv[2])),
        )

        recovery_cmd = BurnCommand(
            burn_id=f"REC-{event.satellite_id}-{uuid.uuid4().hex[:6].upper()}",
            burnTime=recovery_time,
            deltaV_vector=Vec3(x=float(rdv[0]), y=float(rdv[1]), z=float(rdv[2])),
        )

        state_store.add_maneuver(event.satellite_id, evasion_cmd)
        state_store.add_maneuver(event.satellite_id, recovery_cmd)

        logger.info(
            f"[ACM] Burns scheduled for {event.satellite_id} | "
            f"{evasion_cmd.burn_id} @ {evasion_time} | "
            f"{recovery_cmd.burn_id} @ {recovery_time} | "
            f"dir={maneuver['direction']} | "
            f"dv={maneuver['dv_kms'] * 1000:.1f} m/s | "
            f"fuel={maneuver['fuel_cost']:.4f} kg"
        )
        scheduled_count += 1

    return scheduled_count


# ── FIX 6 Helper: Blackout-Aware Evasion Timing ───────────────────────────────

def _resolve_evasion_time(sat_state, tca_seconds: float, default_offset_s: float) -> float:
    """
    Determine when to upload/execute the evasion burn, accounting for
    potential ground-station blackouts between now and TCA.

    Logic (FIX 6):
      1. Check current LOS.
      2. If currently has LOS → check whether LOS is maintained until TCA.
         - Walk forward in small steps; if LOS drops before TCA, use the
           time just before blackout (minus SIGNAL_LATENCY buffer) as the
           upload deadline, and schedule the burn at that deadline.
      3. If currently in blackout → call next_los_window() to find when LOS
         returns, then schedule the burn immediately after that window opens
         (+ SIGNAL_LATENCY).
      4. In all cases ensure offset >= SIGNAL_LATENCY.

    Returns:
        Offset in seconds from now for the evasion burn.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    current_los, _ = has_line_of_sight(sat_state.r, now_iso)

    # ── Case A: Currently in blackout — find next LOS window ─────────────────
    if not current_los:
        window = next_los_window(
            sat_r=sat_state.r,
            sat_v=sat_state.v,
            sim_time_iso=now_iso,
            search_window_s=min(tca_seconds, 7200.0),
        )
        if window["found"]:
            # Upload as soon as LOS returns, respecting SIGNAL_LATENCY
            upload_offset = window["offset_s"] + SIGNAL_LATENCY
            logger.info(
                f"[ACM] Satellite currently in blackout. "
                f"LOS returns in {window['offset_s']:.0f}s — "
                f"evasion burn offset set to {upload_offset:.0f}s"
            )
            return max(upload_offset, SIGNAL_LATENCY)
        else:
            # No LOS found before TCA — use default and warn
            logger.warning(
                f"[ACM] No LOS window found before TCA={tca_seconds:.0f}s — "
                f"using default offset={default_offset_s:.0f}s"
            )
            return max(default_offset_s, SIGNAL_LATENCY)

    # ── Case B: Currently has LOS — check if blackout occurs before TCA ───────
    # Probe LOS in steps up to TCA to find the first blackout instant
    from physics.propagator import rk4_step as _rk4
    import numpy as _np

    probe_state = _np.array(sat_state.r + sat_state.v, dtype=float)
    probe_dt    = 30.0    # seconds per probe step
    elapsed     = 0.0
    blackout_at = None    # offset (seconds from now) when LOS first drops

    while elapsed < tca_seconds:
        step         = min(probe_dt, tca_seconds - elapsed)
        probe_state  = _rk4(probe_state, step)
        elapsed     += step

        future_iso   = _iso_offset(elapsed)
        future_los, _ = has_line_of_sight(probe_state[:3].tolist(), future_iso)

        if not future_los:
            blackout_at = elapsed
            break

    if blackout_at is not None:
        # Schedule burn just before blackout starts, with SIGNAL_LATENCY margin
        safe_offset = max(blackout_at - SIGNAL_LATENCY, SIGNAL_LATENCY)
        logger.info(
            f"[ACM] LOS will drop at T+{blackout_at:.0f}s (before TCA={tca_seconds:.0f}s). "
            f"Scheduling evasion burn at T+{safe_offset:.0f}s to beat blackout."
        )
        return safe_offset

    # No blackout before TCA — use the default half-TCA offset
    return max(default_offset_s, SIGNAL_LATENCY)


def _iso_offset(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()