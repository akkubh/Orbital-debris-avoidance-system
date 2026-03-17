from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

# ── Physical constants ────────────────────────────────────────────────
DRY_MASS_KG       = 500.0
INITIAL_FUEL_KG   = 50.0
INITIAL_WET_MASS  = DRY_MASS_KG + INITIAL_FUEL_KG   # 550 kg
ISP               = 300.0          # seconds
G0                = 9.80665e-3     # km/s²  (converted from m/s²)
MAX_DV_PER_BURN   = 0.015          # km/s  (= 15 m/s)
THRUSTER_COOLDOWN = 600.0          # seconds
SIGNAL_LATENCY    = 10.0           # seconds
CONJUNCTION_DIST  = 0.100          # km  (100 m)
STATION_BOX_KM    = 10.0           # km radius
EOL_FUEL_FRAC     = 0.05           # 5% of initial fuel


@dataclass
class SpaceObject:
    id: str
    type: str                         # "SAT" or "DEBRIS"
    r: list                           # [x, y, z] km  ECI
    v: list                           # [vx, vy, vz] km/s  ECI
    # Satellite-only fields
    fuel_kg: float             = INITIAL_FUEL_KG
    dry_mass_kg: float         = DRY_MASS_KG
    status: str                = "NOMINAL"   # NOMINAL | MANEUVERING | EOL | DEAD
    last_burn_time: Optional[float] = None   # sim seconds epoch
    nominal_r: Optional[list]  = None        # nominal slot position (km ECI)
    nominal_v: Optional[list]  = None        # nominal slot velocity (km/s ECI)

    @property
    def wet_mass(self) -> float:
        return self.dry_mass_kg + self.fuel_kg

    @property
    def fuel_fraction(self) -> float:
        return self.fuel_kg / INITIAL_FUEL_KG


@dataclass
class ScheduledBurn:
    burn_id: str
    satellite_id: str
    burn_time_iso: str          # ISO 8601
    burn_time_epoch: float      # seconds since J2000 (for sorting)
    delta_v_eci: list           # [dvx, dvy, dvz] km/s  already in ECI
    executed: bool = False


@dataclass
class CDMWarning:
    sat_id: str
    deb_id: str
    tca_epoch: float            # time of closest approach (sim seconds)
    miss_distance_km: float
    resolved: bool = False


# ── Singleton state ───────────────────────────────────────────────────
class SimulationState:
    def __init__(self):
        self.objects: dict[str, SpaceObject] = {}      # id → SpaceObject
        self.burns: list[ScheduledBurn]      = []      # pending burns, sorted by time
        self.cdm_warnings: list[CDMWarning]  = []      # active conjunction warnings
        self.sim_time: Optional[datetime]    = None    # current simulation time
        self.sim_epoch: float                = 0.0     # seconds since sim start
        self.total_collisions: int           = 0
        self.total_maneuvers_executed: int   = 0

    # ── helpers ───────────────────────────────────────────────────────
    def get_satellites(self) -> list[SpaceObject]:
        return [o for o in self.objects.values() if o.type == "SAT"]

    def get_debris(self) -> list[SpaceObject]:
        return [o for o in self.objects.values() if o.type == "DEBRIS"]

    def active_cdm_count(self) -> int:
        return sum(1 for w in self.cdm_warnings if not w.resolved)

    def pending_burns_for(self, satellite_id: str) -> list[ScheduledBurn]:
        return [b for b in self.burns
                if b.satellite_id == satellite_id and not b.executed]

    def last_burn_epoch_for(self, satellite_id: str) -> Optional[float]:
        executed = [b for b in self.burns
                    if b.satellite_id == satellite_id and b.executed]
        if not executed:
            return None
        return max(b.burn_time_epoch for b in executed)


# Single global instance — import this everywhere
state = SimulationState()


import json, os

SAVE_FILE = "sim_state.json"

def save_state():
    data = {
        "sim_time": state.sim_time,
        "sim_epoch": state.sim_epoch,
        "objects": {
            id: {
                "id": obj.id, "type": obj.type,
                "r": obj.r, "v": obj.v,
                "fuel_kg": obj.fuel_kg,
                "status": obj.status,
                "nominal_r": obj.nominal_r,
                "nominal_v": obj.nominal_v,
            }
            for id, obj in state.objects.items()
        }
    }
    with open(SAVE_FILE, "w") as f:
        json.dump(data, f)

def load_state():
    if not os.path.exists(SAVE_FILE):
        return
    with open(SAVE_FILE) as f:
        data = json.load(f)
    state.sim_time  = data.get("sim_time")
    state.sim_epoch = data.get("sim_epoch", 0.0)
    for id, obj in data.get("objects", {}).items():
        state.objects[id] = SpaceObject(
            id        = obj["id"],
            type      = obj["type"],
            r         = obj["r"],
            v         = obj["v"],
            fuel_kg   = obj.get("fuel_kg", INITIAL_FUEL_KG),
            status    = obj.get("status", "NOMINAL"),
            nominal_r = obj.get("nominal_r"),
            nominal_v = obj.get("nominal_v"),
        )

# Auto-load on import
load_state()