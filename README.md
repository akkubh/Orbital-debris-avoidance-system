# Orbital Debris Avoidance System (ACM)
### National Space Hackathon 2026 — IIT Delhi × ISRO

A real-time autonomous constellation management system that tracks orbital debris,
detects conjunction events, and schedules evasion burns — all visualized in a
live 3D mission control dashboard.

---

## Demo

- 🌍 3D Earth globe with real-time satellite + debris visualization
- ⚠️ Automatic CDM (Conjunction Data Message) warnings
- 🚀 Autonomous evasion burn scheduling
- 🎯 Manual burn scheduling from Mission Control UI
- 📡 Live TLE data ingestion from CelesTrak
- 💥 Collision detection with full-screen alert overlay

---

## Project Structure
```
orbital-system/
├── backend/
│   ├── main.py                    # FastAPI app + health + reset endpoints
│   ├── api/
│   │   ├── telemetry.py           # POST /api/telemetry
│   │   ├── maneuver.py            # POST /api/maneuver/schedule + /quick
│   │   ├── simulate.py            # POST /api/simulate/step + GET /api/visualization/snapshot
│   │   └── celestrak.py           # POST /api/data/load-celestrak
│   ├── models/
│   │   ├── state_store.py         # Singleton SimulationState + physics constants
│   │   └── schemas.py             # Pydantic request/response schemas
│   ├── physics/
│   │   ├── propagator.py          # RK4+J2 integrator + TLE→state vector
│   │   ├── conjunction.py         # KD-tree screening + TCA computation
│   │   ├── maneuver_calc.py       # Evasion / recovery burn planning
│   │   ├── risk_model.py          # Risk scoring: LOW/MEDIUM/HIGH/CRITICAL
│   │   └── ground_station.py      # LOS visibility (6 real ground stations)
│   ├── data/
│   │   └── tle_fetcher.py         # CelesTrak GP API fetch + LEO filter
│   └── utils/
│       ├── generate_test_data.py  # Injects 50 sats + 500 debris via API
│       └── test_conjunction.py    # Places debris 50m from SAT-CONJ-TEST
├── orbital-dashboard/             # Vite + React frontend
│   └── src/
│       ├── App.jsx
│       ├── main.tsx
│       ├── components/
│       │   ├── SatelliteScene.tsx # Main 3D scene + polling + control buttons
│       │   ├── OrbitalShell.tsx   # Satellite point cloud (up to 10,000)
│       │   └── MissionControl.jsx # CDM warnings / burns / health / manual burn
│       └── services/
│           └── api.js             # Axios client for all backend calls
└── data/
    └── sim_state.json             # Persisted simulation state (auto-generated)
```

---

## Quick Start

### 1. Backend
```bash
cd backend
pip install fastapi uvicorn sgp4 scipy numpy requests
python -m uvicorn main:app --reload --port 8000
```

### 2. Frontend
```bash
cd orbital-dashboard
npm install
npm run dev
# → http://localhost:5173
```

### 3. Load Data (choose one)
```bash
# Option A — Real TLE data from CelesTrak
# Click "🛰️ Load CelesTrak" in the UI

# Option B — Synthetic test data (50 sats + 500 debris)
cd backend
python utils/generate_test_data.py
```

### 4. Test Conjunction Detection
```bash
cd backend
python utils/test_conjunction.py
# Places debris 50m from SAT-CONJ-TEST → HIGH risk → auto-evasion burn fires
```

### 5. Reset Between Runs

Click **"🗑️ Reset State"** in the UI, or:
```bash
curl -X POST http://localhost:8000/api/reset
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check — object counts, sim time, CDM count |
| GET | `/api/state` | Full debug state dump |
| GET | `/api/mission` | CDM warnings, burn queue, satellite health |
| POST | `/api/reset` | Clear all state |
| POST | `/api/telemetry` | Ingest positions → conjunction screen → auto-evasion |
| POST | `/api/maneuver/schedule` | Queue a full burn sequence |
| POST | `/api/maneuver/quick` | Schedule single burn (direction + magnitude + delay) |
| POST | `/api/simulate/step` | Advance simulation by N seconds |
| GET | `/api/visualization/snapshot` | Current x,y,z positions for 3D rendering |
| POST | `/api/data/load-celestrak` | Fetch live TLE data from CelesTrak |

---

## Physics Engine

| Component | Implementation |
|-----------|---------------|
| Orbital propagation | RK4 + J2 perturbation (vectorised, batched for debris) |
| Conjunction detection | KD-tree O(N log N) + TCA computation |
| Evasion planning | RTN frame burns, Tsiolkovsky fuel model, ISP=300s |
| Risk classification | LOW / MEDIUM / HIGH / CRITICAL with miss-distance floor |
| Ground station LOS | 6 real stations, line-of-sight before burn scheduling |
| TLE ingestion | SGP4 via `sgp4` library, LEO filter (alt < 2000 km) |

---

## Demo Sequence
```
1. Start backend + frontend
2. Click "🛰️ Load CelesTrak"        → 50 real satellites + 500 debris load
3. Run python utils/test_conjunction.py
4. Watch Mission Control panel:
   ⚠️  SAT-CONJ-TEST ↔ DEB-CONJ-001  HIGH  miss=50m
   🚀  EVA burn auto-scheduled, fires on next sim step
5. Satellite dot turns YELLOW (MANEUVERING) → CYAN (NOMINAL) after burn
6. Expand "🎯 Schedule Manual Burn" → pick satellite, direction, ΔV, delay
7. If evasion fails: 💥 full-screen collision flash + DEAD status
```

---

## Tech Stack

**Backend:** Python 3.14, FastAPI, Uvicorn, NumPy, SciPy, SGP4, Pydantic  
**Frontend:** React 18, Vite, TypeScript, Three.js, React Three Fiber, Zustand, Axios  
**Data:** CelesTrak GP API (live TLE), synthetic test data generator  
**Deployment:** Docker (single container, port 8000)

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ACM_DATA_DIR` | `../../data` | Where `sim_state.json` is saved |
| `ACM_CORS_ORIGINS` | `http://localhost:5173,...` | Allowed frontend origins |

---

## Docker
```bash
docker build -t orbital-acm .
docker run -p 8000:8000 orbital-acm
```

---

*Built for National Space Hackathon 2026 — IIT Delhi × ISRO*
