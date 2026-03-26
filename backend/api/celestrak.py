from fastapi import APIRouter
from data.tle_fetcher import fetch_debris_tles, fetch_satellite_tles
from physics.propagator import tle_to_state_vector
from models.state_store import state

router = APIRouter()

@router.post("/api/data/load-celestrak")
async def load_celestrak_data(max_debris: int = 500, max_sats: int = 50):
    # --- 1. Fetch raw data from the internet ---
    debris_tles = fetch_debris_tles()
    sat_tles = fetch_satellite_tles()

    print(f"DEBUG: Internet Fetch - Got {len(sat_tles)} satellites and {len(debris_tles)} debris.")

    # --- 2. Process Satellites ---
    sat_count = 0
    for s in sat_tles[:max_sats]:
        sv = tle_to_state_vector(s["line1"], s["line2"])
        if sv:
            state.update_satellite(
                satellite_id=s["name"],
                position=sv["position"],
                velocity=sv["velocity"]
            )
            sat_count += 1
        else:
            print(f"DEBUG: Physics failed for satellite: {s.get('name', 'Unknown')}")

    # --- 3. Process Debris ---
    deb_count = 0
    for d in debris_tles[:max_debris]:
        sv = tle_to_state_vector(d["line1"], d["line2"])
        if sv:
            state.update_debris(
                debris_id=d["name"],
                position=sv["position"],
                velocity=sv["velocity"]
            )
            deb_count += 1
        else:
            print(f"DEBUG: Physics failed for debris: {d.get('name', 'Unknown')}")

    # --- 4. Return Final Counts ---
    return {
        "status": "loaded",
        "satellites": sat_count,
        "debris": deb_count
    }