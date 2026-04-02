"""
test_conjunction.py
Places debris on a converging trajectory so TCA is ~300s in the future,
giving the auto-evasion system time to schedule and fire a burn.
"""
import requests
from datetime import datetime, timezone, timedelta

BASE = "http://localhost:8000"

def inject_conjunction():
    now = datetime.now(timezone.utc)

    # Step 1: Register SAT-CONJ-TEST in a clean LEO orbit
    # Position: ~550km altitude on equatorial plane
    sat_r = [6928.0, 0.0, 0.0]       # km ECI — 550km altitude
    sat_v = [0.0, 7.6, 0.0]          # km/s — circular LEO velocity

    # Step 2: Place debris on converging trajectory
    # Offset debris ahead in the orbit by ~2km (along-track)
    # with a slight inward radial velocity so it closes over ~300 seconds
    deb_r = [6928.0, 2.0, 0.0]       # 2km ahead along-track
    deb_v = [0.0, 7.5935, 0.01]      # slightly slower + tiny radial — converges in ~300s

    payload = {
        "timestamp": now.isoformat(),
        "objects": [
            {
                "id": "SAT-CONJ-TEST",
                "type": "SAT",
                "r": {"x": sat_r[0], "y": sat_r[1], "z": sat_r[2]},
                "v": {"x": sat_v[0], "y": sat_v[1], "z": sat_v[2]},
                "fuel_kg": 50.0
            },
            {
                "id": "DEB-CONJ-NEAR",
                "type": "DEBRIS",
                "r": {"x": deb_r[0], "y": deb_r[1], "z": deb_r[2]},
                "v": {"x": deb_v[0], "y": deb_v[1], "z": deb_v[2]}
            }
        ]
    }

    res = requests.post(f"{BASE}/api/telemetry", json=payload)
    data = res.json()

    print(f"Response: {data}")
    print(f"CDM warnings active: {data.get('active_cdm_warnings', 0)}")
    print()
    print("Expected behaviour:")
    print("  - Mission Control shows SAT-CONJ-TEST ↔ DEB-CONJ-NEAR")
    print("  - TCA should be ~300s in the future (not PAST TCA)")
    print("  - AUTO evasion burn schedules within next sim step (5s)")
    print("  - Satellite dot turns YELLOW (MANEUVERING)")
    print("  - After burn fires: dot returns to CYAN (NOMINAL)")
    print("  - Miss distance increases as trajectories diverge")

if __name__ == "__main__":
    inject_conjunction()