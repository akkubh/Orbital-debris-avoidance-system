"""
api/telemetry.py
━━━━━━━━━━━━━━━
Telemetry Ingestion & Auto-Evasion for ACM (Autonomous Constellation Manager)
National Space Hackathon 2026

Uses exact schema names from models/schemas.py:
    TelemetryRequest  — batch multi-object telemetry payload
    TelemetryResponse — ACK response
    BurnCommand       — individual burn instruction
    Vec3              — 3D vector (x, y, z)

CHANGES vs original:
  • _auto_schedule_evasions() gates on risk_level:
      LOW     → skip entirely (no burn)
      MEDIUM  → log warning only
      HIGH    → schedule evasion + recovery
      CRITICAL→ run optimize_delta_v() first, then schedule
"""

import uuid
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException

# ── Schema imports — exact names from models/schemas.py ──────────────────────
from models.schemas import (
    TelemetryRequest,
    TelemetryResponse,
    BurnCommand,
    Vec3,
)
from models.state_store import state_store
from physics.conjunction import screen_conjunctions
from physics.maneuver_calc import plan_evasion_burn, plan_recovery_burn, optimize_delta_v

logger = logging.getLogger("ACM.Telemetry")

router = APIRouter(tags=["Telemetry"])

AUTO_EVASION_RISK_LEVELS = {"HIGH", "CRITICAL"}


# ── Telemetry Ingestion Endpoint ──────────────────────────────────────────────

@router.post("/api/telemetry", response_model=TelemetryResponse)
async def ingest_telemetry(payload: TelemetryRequest):
    """
    Ingest a batch of satellite + debris telemetry objects.

    Each TelemetryObject has:
        id   : str          — object identifier
        type : "SAT"|"DEBRIS"
        r    : Vec3         — position (km, ECI)
        v    : Vec3         — velocity (km/s, ECI)
    """
    timestamp = payload.timestamp
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

    # Screen conjunctions once per batch
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

    LOW    → skip
    MEDIUM → log only
    HIGH   → schedule evasion + recovery
    CRITICAL → optimize ΔV first, then schedule
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

        recovery_dv_arr = plan_recovery_burn(maneuver["dv_eci"])

        evasion_time  = _iso_offset(event.tca_seconds * 0.5)
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


def _iso_offset(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()