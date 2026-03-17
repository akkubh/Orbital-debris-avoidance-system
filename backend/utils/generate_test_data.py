"""
Test data generator — sends 50 satellites + 500 debris to the API.
Run with: python utils/generate_test_data.py
"""
import random
import math
import urllib.request
import json

API = "http://localhost:8000"
random.seed(42)

def random_leo_object(id, type):
    # Random LEO orbit: altitude 400-800km, so r_norm = 6778-7178 km
    r_norm = random.uniform(6778, 7178)
    # Random unit vector for position
    theta = random.uniform(0, 2 * math.pi)
    phi   = random.uniform(-math.pi/2, math.pi/2)
    x = r_norm * math.cos(phi) * math.cos(theta)
    y = r_norm * math.cos(phi) * math.sin(theta)
    z = r_norm * math.sin(phi)
    # Circular orbit velocity ~7.5 km/s, perpendicular to r
    v_norm = math.sqrt(398600.4418 / r_norm)
    vx = -v_norm * math.sin(theta)
    vy =  v_norm * math.cos(theta)
    vz =  random.uniform(-0.1, 0.1)
    return {"id": id, "type": type,
            "r": {"x": round(x,2), "y": round(y,2), "z": round(z,2)},
            "v": {"x": round(vx,4), "y": round(vy,4), "z": round(vz,4)}}

def post(endpoint, data):
    body = json.dumps(data).encode()
    req  = urllib.request.Request(
        f"{API}{endpoint}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

# Build objects list
objects = []
for i in range(1, 51):
    objects.append(random_leo_object(f"SAT-{i:03d}", "SAT"))
for i in range(1, 501):
    objects.append(random_leo_object(f"DEB-{i:05d}", "DEBRIS"))

print(f"Sending {len(objects)} objects to API...")
result = post("/api/telemetry", {
    "timestamp": "2026-03-17T08:00:00.000Z",
    "objects": objects
})
print(f"Response: {result}")
print(f"\nActive CDM warnings: {result['active_cdm_warnings']}")

# Check snapshot
req = urllib.request.Request(f"{API}/api/visualization/snapshot")
with urllib.request.urlopen(req) as r:
    snap = json.loads(r.read())
print(f"Satellites tracked: {len(snap['satellites'])}")
print(f"Debris tracked: {len(snap['debris_cloud'])}")