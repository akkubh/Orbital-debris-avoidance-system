"""
Orbital Debris Avoidance & Constellation Management System
National Space Hackathon 2026 — Backend API
"""
import logging
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── Logging setup ─────────────────────────────────────────────────────
logging.basicConfig(
    stream  = sys.stdout,
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")

# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "Autonomous Constellation Manager",
    description = "Orbital debris avoidance, conjunction detection, maneuver planning",
    version     = "1.0.0",
)

# Allow frontend (any origin during hackathon)
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Routers ───────────────────────────────────────────────────────────
from api.telemetry import router as telemetry_router
from api.maneuver  import router as maneuver_router
from api.simulate  import router as simulate_router

app.include_router(telemetry_router)
app.include_router(maneuver_router)
app.include_router(simulate_router)


# ── Health check ──────────────────────────────────────────────────────
@app.get("/")
def health():
    from models.state_store import state
    return {
        "status"        : "ONLINE",
        "message"       : "Autonomous Constellation Manager running 🚀",
        "satellites"    : len(state.get_satellites()),
        "debris_objects": len(state.get_debris()),
        "sim_time"      : state.sim_time,
        "cdm_warnings"  : state.active_cdm_count(),
    }



# ── Debug: raw state dump ─────────────────────────────────────────────
@app.get("/api/state")
def raw_state():
    """Shows everything currently in memory — useful for debugging."""
    from models.state_store import state
    return {
        "sim_time":   state.sim_time,
        "sim_epoch":  state.sim_epoch,
        "satellites": {
            id: {"r": obj.r, "v": obj.v, "fuel_kg": obj.fuel_kg, "status": obj.status}
            for id, obj in state.objects.items() if obj.type == "SAT"
        },
        "debris": {
            id: {"r": obj.r, "v": obj.v}
            for id, obj in state.objects.items() if obj.type == "DEBRIS"
        },
        "pending_burns": len([b for b in state.burns if not b.executed]),
        "cdm_warnings":  state.active_cdm_count(),
    }

if __name__ == "__main__":
    import uvicorn
    log.info("Starting ACM server on 0.0.0.0:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)