# 🛰️ Orbital Debris Avoidance & Constellation Management System
### National Space Hackathon 2026 — IIT Delhi × ISRO

## Overview
An Autonomous Constellation Manager (ACM) that detects orbital conjunctions,
plans evasion maneuvers, and manages a fleet of 50+ satellites in real time.

## Features
- ⚡ Real-time orbital propagation (RK4 + J2 perturbation)
- 🔍 KD-tree conjunction detection (O(N log N))
- 🚀 Autonomous evasion burn planning (RTN frame)
- ⛽ Tsiolkovsky fuel tracking per satellite
- 📡 Ground station Line-of-Sight verification
- ☠️ EOL graveyard orbit auto-management

## API Endpoints
| Endpoint | Method | Description |
|---|---|---|
| `/api/telemetry` | POST | Ingest satellite/debris state vectors |
| `/api/maneuver/schedule` | POST | Schedule evasion burns |
| `/api/simulate/step` | POST | Advance simulation time |
| `/api/visualization/snapshot` | GET | Frontend data snapshot |

## Running Locally
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

## Docker
```bash
docker build -t orbital-acm .
docker run -p 8000:8000 orbital-acm
```

## Architecture
```
backend/
├── main.py                  ← FastAPI entry point
├── physics/
│   ├── propagator.py        ← RK4 + J2 orbital mechanics
│   ├── conjunction.py       ← KD-tree collision detection
│   ├── maneuver_calc.py     ← RTN/ECI burns + Tsiolkovsky
│   └── ground_station.py   ← LOS verification
├── models/
│   ├── state_store.py       ← Global simulation state
│   └── schemas.py           ← Pydantic request/response models
└── api/
    ├── telemetry.py         ← POST /api/telemetry
    ├── maneuver.py          ← POST /api/maneuver/schedule
    └── simulate.py          ← POST /api/simulate/step + GET /api/visualization/snapshot
```

## Tech Stack
- **Backend:** Python, FastAPI, Uvicorn
- **Physics:** NumPy, SciPy (KD-tree)
- **Frontend:** React, Three.js / CesiumJS
- **Deployment:** Docker (ubuntu:22.04)

## Team
- Member 1: Backend + API Engineer
- Member 2: AI / Collision Logic Engineer
- Member 3: Frontend + Visualization Engineer