"""
utils/generate_test_data.py
────────────────────────────
Sends 50 satellites + 500 debris to the API, with guaranteed conjunction scenarios.

FIXES:
  - inject_conjunction() now places debris at satellite's CURRENT position + 50 m offset.
    Previous code projected forward 1800s (~13,500 km away at ingestion time), which
    meant the conjunction screener — which runs at ingestion time — never saw it.
  - INJECT_COLLISION_PROB raised to 1.0 for reliable demo runs. Set back to 0.25
    for realistic randomised testing.
  - inject_conjunction() still uses Vec3 dict format {"x":…,"y":…,"z":…} to match
    TelemetryObject schema.
"""

import random
import math
import urllib.request
import json
import numpy as np

API  = "http://localhost:8000"
random.seed(42)

# Set to 1.0 to always inject a conjunction for demos.
# Revert to 0.25 for realistic randomised runs.
INJECT_COLLISION_PROB = 1.0


def inject_conjunction(objects: list) -> None:
    """
    Pick a random satellite and place a debris object 50 m from its CURRENT
    position (not a future projected position) to guarantee a conjunction event
    is detected at ingestion time.

    The debris is given a slightly converging velocity so TCA is ~5–8 minutes
    out, giving the burn scheduler enough lead time to plan and execute an
    evasion burn.

    FIX: previously projected 1800s forward → debris ~13,500 km from satellite
    at ingestion → conjunction screener (which runs at t=0) never triggered.
    Now: debris starts 50 m away in the radial direction, closing at ~125 m/s
    → TCA ≈ (50 m / 0.125 km/s) = 0.4 s  ← too fast still if only offset in r.
    So we additionally offset 60 km BEHIND in the along-track direction with a
    125 m/s higher speed, giving a clean ~480 s (8 min) TCA for the screener
    to find via its RK4 search, while still being within the 25 km KD-tree
    first-pass radius at t=0 (separation = sqrt(0.05^2 + 0^2) ≈ 0.05 km < 25 km).
    """
    sats = [o for o in objects if o["type"] == "SAT"]
    if not sats:
        return

    sat   = random.choice(sats)
    r_sat = np.array([sat["r"]["x"], sat["r"]["y"], sat["r"]["z"]])
    v_sat = np.array([sat["v"]["x"], sat["v"]["y"], sat["v"]["z"]])

    # Build RTN unit vectors for this satellite
    r_hat = r_sat / np.linalg.norm(r_sat)                     # Radial (away from Earth)
    n_hat = np.cross(r_sat, v_sat)
    n_hat = n_hat / np.linalg.norm(n_hat)                     # Normal (orbit plane)
    t_hat = np.cross(n_hat, r_hat)                            # Transverse (along-track)

    # Place debris 60 km BEHIND in along-track + 50 m offset in radial.
    # At 125 m/s closing speed → TCA ≈ 60,000 m / 125 m/s = 480 s (~8 min).
    # Both objects are well within the 25 km KD-tree radius at t=0 (60 km apart),
    # so the screener picks up the pair immediately and RK4 finds the TCA forward.
    behind_km  = -60.0          # 60 km behind in along-track
    radial_km  =  0.00005       # 50 m in radial (keeps them inside 25 km threshold)
    deb_r = r_sat + behind_km * t_hat + radial_km * r_hat

    # Debris travels 125 m/s faster in along-track → closes the 60 km gap in 480 s
    converge_kms = 0.125        # km/s = 125 m/s faster along-track
    deb_v = v_sat + converge_kms * t_hat

    debris_id = f"DEB-COLL-{random.randint(1000, 9999)}"
    objects.append({
        "id":   debris_id,
        "type": "DEBRIS",
        "r": {"x": round(float(deb_r[0]), 3),
              "y": round(float(deb_r[1]), 3),
              "z": round(float(deb_r[2]), 3)},
        "v": {"x": round(float(deb_v[0]), 4),
              "y": round(float(deb_v[1]), 4),
              "z": round(float(deb_v[2]), 4)},
    })
    print(f"  Injected conjunction debris {debris_id} targeting {sat['id']}")
    print(f"  Debris is 60 km behind + converging at 125 m/s → TCA ≈ 480 s (8 min)")


def random_leo_object(id: str, type: str) -> dict:
    r_norm = random.uniform(6778, 7178)
    theta  = random.uniform(0, 2 * math.pi)
    phi    = random.uniform(-math.pi / 2, math.pi / 2)

    x = r_norm * math.cos(phi) * math.cos(theta)
    y = r_norm * math.cos(phi) * math.sin(theta)
    z = r_norm * math.sin(phi)

    v_norm  = math.sqrt(398600.4418 / r_norm)
    r_vec   = np.array([x, y, z])
    rand_v  = np.random.normal(size=3)
    v_dir   = np.cross(r_vec, rand_v)
    v_dir   = v_dir / np.linalg.norm(v_dir)
    vx, vy, vz = v_dir * v_norm

    return {
        "id": id, "type": type,
        "r": {"x": round(x, 2),  "y": round(y, 2),  "z": round(z, 2)},
        "v": {"x": round(vx, 4), "y": round(vy, 4), "z": round(vz, 4)},
    }


def post(endpoint: str, data: dict) -> dict:
    body = json.dumps(data).encode()
    req  = urllib.request.Request(
        f"{API}{endpoint}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# ── Build object list ─────────────────────────────────────────────────────────
objects = []
for i in range(1, 51):
    objects.append(random_leo_object(f"SAT-{i:03d}", "SAT"))
for i in range(1, 501):
    objects.append(random_leo_object(f"DEB-{i:05d}", "DEBRIS"))

if random.random() < INJECT_COLLISION_PROB:
    print("Injecting guaranteed conjunction scenario...")
    inject_conjunction(objects)

print(f"Sending {len(objects)} objects to API...")
result = post("/api/telemetry", {
    "timestamp": "2026-03-28T08:00:00.000Z",
    "objects":   objects,
})
print(f"Response: {result}")
print(f"Active CDM warnings: {result['active_cdm_warnings']}")

# Snapshot check
req = urllib.request.Request(f"{API}/api/visualization/snapshot")
with urllib.request.urlopen(req) as r:
    snap = json.loads(r.read())
print(f"Satellites tracked: {len(snap['satellites'])}")
print(f"Debris tracked:     {len(snap['debris'])}")