"""
main.py — ACM FastAPI entry point
"""
import logging
import os
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")

app = FastAPI(
    title="Autonomous Constellation Manager",
    description="Orbital debris avoidance, conjunction detection, maneuver planning",
    version="1.0.0",
)

_cors_origins = os.environ.get(
    "ACM_CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.telemetry  import router as telemetry_router
from api.maneuver   import router as maneuver_router
from api.simulate   import router as simulate_router
from api.celestrak  import router as celestrak_router

app.include_router(telemetry_router)
app.include_router(maneuver_router)
app.include_router(simulate_router)
app.include_router(celestrak_router)


@app.get("/")
def health():
    from models.state_store import state
    return {
        "status":                   "ONLINE",
        "satellites":               len(state.get_satellites()),
        "debris_objects":           len(state.get_debris()),
        "sim_time":                 state.sim_time,
        "sim_epoch":                round(state.sim_epoch, 1),
        "cdm_warnings":             state.active_cdm_count(),
        "total_collisions":         state.total_collisions,
        "total_maneuvers_executed": state.total_maneuvers_executed,
        "pending_burns":            sum(1 for b in state.burns if not b.executed),
    }


@app.get("/api/state")
def raw_state():
    from models.state_store import state
    return {
        "sim_time":                 state.sim_time,
        "sim_epoch":                state.sim_epoch,
        "satellites":               {
            id: {"r": obj.r, "v": obj.v, "fuel_kg": obj.fuel_kg, "status": obj.status}
            for id, obj in state.objects.items() if obj.type == "SAT"
        },
        "debris":                   {
            id: {"r": obj.r, "v": obj.v}
            for id, obj in state.objects.items() if obj.type == "DEBRIS"
        },
        "pending_burns":            len([b for b in state.burns if not b.executed]),
        "cdm_warnings":             state.active_cdm_count(),
        "total_collisions":         state.total_collisions,
        "total_maneuvers_executed": state.total_maneuvers_executed,
    }


@app.post("/api/reset")
def reset_state():
    """Clear all simulation state. Use before loading fresh telemetry data."""
    from models.state_store import state, clear_save_file
    state.objects.clear()
    state.burns.clear()
    state.cdm_warnings.clear()
    state.sim_time               = None
    state.sim_epoch              = 0.0
    state.total_collisions       = 0
    state.total_maneuvers_executed = 0
    state._state_loaded          = True   # prevent re-loading old file
    clear_save_file()
    log.info("Simulation state RESET — all objects, burns, warnings cleared")
    return {"status": "RESET", "message": "All state cleared."}



@app.get("/api/mission")
def mission_status():
    """
    Rich mission status: CDM warnings with full detail, scheduled burns,
    collision events, and satellite health. Used by the Mission Control panel.
    """
    from models.state_store import state
    import math

    # Full CDM warning detail
    cdm = []
    for w in state.cdm_warnings:
        if w.resolved:
            continue
        # Find any pending evasion burn for this satellite
        evasion_pending = any(
            not b.executed and b.satellite_id == w.sat_id and b.burn_id.startswith("EVA-")
            for b in state.burns
        )
        # Find sat fuel
        sat_obj = state.objects.get(w.sat_id)
        fuel = round(sat_obj.fuel_kg, 1) if sat_obj else None
        time_to_tca = round(w.tca_epoch - state.sim_epoch, 0)
        cdm.append({
            "sat_id":          w.sat_id,
            "deb_id":          w.deb_id,
            "risk_level":      w.risk_level,
            "miss_distance_km": round(w.miss_distance_km, 4),
            "time_to_tca_s":   time_to_tca,
            "evasion_pending": evasion_pending,
            "sat_fuel_kg":     fuel,
        })

    # Scheduled burns (pending only)
    burns = []
    for b in state.burns:
        if b.executed:
            continue
        dv_mag = math.sqrt(sum(x**2 for x in b.delta_v_eci))
        burns.append({
            "burn_id":        b.burn_id,
            "satellite_id":   b.satellite_id,
            "burn_time_iso":  b.burn_time_iso,
            "burn_time_epoch": round(b.burn_time_epoch, 1),
            "delta_v_ms":     round(dv_mag * 1000, 3),   # km/s → m/s
        })

    # Satellite health summary
    sats = []
    for obj in state.get_satellites():
        sats.append({
            "id":         obj.id,
            "status":     obj.status,
            "fuel_kg":    round(obj.fuel_kg, 2),
            "fuel_pct":   round(obj.fuel_fraction * 100, 1),
        })

    # Recent collisions (DEAD satellites)
    dead = [
        {"id": obj.id, "fuel_kg": round(obj.fuel_kg, 2)}
        for obj in state.get_satellites()
        if obj.status == "DEAD"
    ]

    return {
        "sim_epoch":                round(state.sim_epoch, 1),
        "sim_time":                 state.sim_time,
        "total_collisions":         state.total_collisions,
        "total_maneuvers_executed": state.total_maneuvers_executed,
        "active_cdm_warnings":      cdm,
        "scheduled_burns":          burns,
        "satellite_health":         sats,
        "dead_satellites":          dead,
    }

if __name__ == "__main__":
    import uvicorn
    log.info("Starting ACM server on 0.0.0.0:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)