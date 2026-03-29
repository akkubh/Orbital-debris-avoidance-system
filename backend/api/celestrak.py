import math
import logging
from fastapi import APIRouter
from data.tle_fetcher import fetch_debris_tles, fetch_satellite_tles
from physics.propagator import tle_to_state_vector
from models.state_store import state, save_state

router  = APIRouter()
log     = logging.getLogger("celestrak")

# Only load satellites within LEO (< 2000 km altitude) so they appear
# in the main orbital ring. GEO/MEO satellites at 36000-42000 km altitude
# scale to 5-7 Three.js units and scatter far outside the visible scene.
LEO_MAX_ALTITUDE_KM = 2000.0
EARTH_RADIUS_KM     = 6371.0
LEO_MAX_R_KM        = EARTH_RADIUS_KM + LEO_MAX_ALTITUDE_KM   # 8371 km


@router.post("/api/data/load-celestrak")
async def load_celestrak_data(max_debris: int = 500, max_sats: int = 50):
    debris_tles = fetch_debris_tles()
    sat_tles    = fetch_satellite_tles()
    log.info(f"CelesTrak fetch: {len(sat_tles)} satellites, {len(debris_tles)} debris")

    # ── Satellites (LEO only) ─────────────────────────────────────────────────
    sat_count   = 0
    sat_skipped = 0
    for s in sat_tles:
        if sat_count >= max_sats:
            break
        sv = tle_to_state_vector(s["line1"], s["line2"])
        if not sv:
            continue
        r_mag = math.sqrt(sum(x**2 for x in sv["position"]))
        if r_mag > LEO_MAX_R_KM:
            sat_skipped += 1
            continue
        state.update_satellite(
            satellite_id=s["name"],
            position=sv["position"],
            velocity=sv["velocity"],
        )
        sat_count += 1

    # ── Debris ────────────────────────────────────────────────────────────────
    deb_count = 0
    for d in debris_tles[:max_debris]:
        sv = tle_to_state_vector(d["line1"], d["line2"])
        if sv:
            state.update_debris(
                debris_id=d["name"],
                position=sv["position"],
                velocity=sv["velocity"],
            )
            deb_count += 1

    save_state()   # persist after full batch

    log.info(
        f"CelesTrak loaded: {sat_count} LEO sats "
        f"({sat_skipped} non-LEO skipped), {deb_count} debris"
    )
    return {
        "status":         "loaded",
        "satellites":     sat_count,
        "debris":         deb_count,
        "skipped_non_leo": sat_skipped,
    }