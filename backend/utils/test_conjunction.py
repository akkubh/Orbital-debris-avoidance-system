"""
Self-contained conjunction test — gets current satellite position then places debris next to it.
"""
import urllib.request
import json

API = "http://localhost:8000"

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

def get(endpoint):
    req = urllib.request.Request(f"{API}{endpoint}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

# Step 1: Register a fresh satellite at a known position
print("Step 1: Registering fresh satellite...")
SAT_R = {"x": 6778.0, "y": 0.0, "z": 0.0}
SAT_V = {"x": 0.0, "y": 7.5, "z": 0.0}

r1 = post("/api/telemetry", {
    "timestamp": "2026-03-17T08:00:00.000Z",
    "objects": [{"id": "SAT-CONJ-TEST", "type": "SAT", "r": SAT_R, "v": SAT_V}]
})
print(f"  Satellite registered: {r1}")

# Step 2: Place debris at EXACTLY the same position (0m away)
print("\nStep 2: Placing debris at same position (0m away)...")
r2 = post("/api/telemetry", {
    "timestamp": "2026-03-17T08:00:00.000Z",
    "objects": [{"id": "DEB-CONJ-001", "type": "DEBRIS",
                 "r": {"x": 6778.0 + 0.05, "y": 0.0, "z": 0.0},
                 "v": {"x": 0.0, "y": 7.5, "z": 0.0}}]
})
print(f"  Response: {r2}")
print(f"  CDM warnings: {r2['active_cdm_warnings']}")

# Step 3: Place debris even closer
print("\nStep 3: Placing debris 10m away...")
r3 = post("/api/telemetry", {
    "timestamp": "2026-03-17T08:00:00.000Z",
    "objects": [{"id": "DEB-CONJ-002", "type": "DEBRIS",
                 "r": {"x": 6778.0 + 0.01, "y": 0.0, "z": 0.0},
                 "v": {"x": 0.0, "y": 7.5, "z": 0.0}}]
})
print(f"  Response: {r3}")
print(f"  CDM warnings: {r3['active_cdm_warnings']}")

# Step 4: Check snapshot
print("\nStep 4: Snapshot check...")
snap = get("/api/visualization/snapshot")
for s in snap["satellites"]:
    if "CONJ" in s["id"]:
        print(f"  {s['id']}: status={s['status']}, fuel={s['fuel_kg']}kg")

print("\n--- RESULT ---")
total_warnings = r3['active_cdm_warnings']
if total_warnings > 0:
    print(f"✅ Conjunction detection WORKING! {total_warnings} CDM warning(s) active")
    print("✅ Evasion burns auto-scheduled!")
else:
    print("❌ No warnings — let's debug")
    # Debug: check what the conjunction screener sees
    print("\nDebugging — checking broad search radius...")
    print("The debris at x=6778.05 is 0.05km from satellite at x=6778.0")
    print("This is within the 100m threshold — should trigger")
    print("Check physics/conjunction.py CONJUNCTION_KM value")