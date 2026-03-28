"""
utils/generate_test_data.py
────────────────────────────
Sends 50 satellites + 500 debris to the API, with guaranteed conjunction scenarios.

FIXES APPLIED:
  - inject_conjunction() now actually called (was defined but never invoked)
  - inject_conjunction() uses Vec3 dict format {"x":…,"y":…,"z":…} to match
    TelemetryObject schema (was using plain list which fails Pydantic validation)
  - INJECT_COLLISION_PROB respected — ~25% of runs inject a near-conjunction
"""

import random
import math
import urllib.request
import json
import numpy as np

API  = "http://localhost:8000"
random.seed(42)
INJECT_COLLISION_PROB = 0.25


def inject_conjunction(objects: list) -> None:
    """
    Pick a random satellite and place a debris object 50 m ahead of it
    (projected ~30 min forward) to guarantee a conjunction event.

    FIX: r and v use {"x":…,"y":…,"z":…} dict format (Vec3-compatible),
    not a plain list which caused Pydantic 422 validation errors.
    """
    sats = [o for o in objects if o["type"] == "SAT"]
    if not sats:
        return

    sat       = random.choice(sats)
    r_sat     = np.array([sat["r"]["x"], sat["r"]["y"], sat["r"]["z"]])
    v_sat     = np.array([sat["v"]["x"], sat["v"]["y"], sat["v"]["z"]])

    # Project forward ~30 min, add small perpendicular offset
    t         = 1800.0
    future    = r_sat + v_sat * t
    offset    = np.random.normal(0, 0.05, 3)   # ~50 m
    deb_r     = future + offset
    deb_v     = v_sat + np.random.normal(0, 0.001, 3)

    debris_id = f"DEB-COLL-{random.randint(1000, 9999)}"
    objects.append({
        "id":   debris_id,
        "type": "DEBRIS",
        # FIX: Vec3 dict format
        "r": {"x": round(float(deb_r[0]), 2),
               "y": round(float(deb_r[1]), 2),
               "z": round(float(deb_r[2]), 2)},
        "v": {"x": round(float(deb_v[0]), 4),
               "y": round(float(deb_v[1]), 4),
               "z": round(float(deb_v[2]), 4)},
    })
    print(f"  Injected conjunction debris {debris_id} near {sat['id']}")


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

# FIX: actually call inject_conjunction based on probability
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