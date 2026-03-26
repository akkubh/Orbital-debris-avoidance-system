from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

# ── Physical constants ────────────────────────────────────────────────
DRY_MASS_KG       = 500.0
INITIAL_FUEL_KG   = 50.0
INITIAL_WET_MASS  = DRY_MASS_KG + INITIAL_FUEL_KG
ISP               = 300.0
G0                = 9.80665e-3
MAX_DV_PER_BURN   = 0.015
THRUSTER_COOLDOWN = 600.0
SIGNAL_LATENCY    = 10.0
CONJUNCTION_DIST  = 0.100
STATION_BOX_KM    = 10.0
EOL_FUEL_FRAC     = 0.05


@dataclass
class SpaceObject:
    id: str
    type: str
    r: list
    v: list
    fuel_kg: float             = INITIAL_FUEL_KG
    dry_mass_kg: float         = DRY_MASS_KG
    status: str                = "NOMINAL"
    last_burn_time: Optional[float] = None
    nominal_r: Optional[list]  = None
    nominal_v: Optional[list]  = None

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
    burn_time_iso: str
    burn_time_epoch: float
    delta_v_eci: list
    executed: bool = False


@dataclass
class CDMWarning:
    sat_id: str
    deb_id: str
    tca_epoch: float
    miss_distance_km: float
    resolved: bool = False


class SimulationState:
    def __init__(self):
        self.objects: dict[str, SpaceObject] = {}
        self.burns: list[ScheduledBurn]      = []
        self.cdm_warnings: list[CDMWarning]  = []
        self.sim_time: Optional[datetime]    = None
        self.sim_epoch: float                = 0.0
        self.total_collisions: int           = 0
        self.total_maneuvers_executed: int   = 0

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

    @property
    def satellites(self) -> dict[str, "SpaceObject"]:
        return {id: obj for id, obj in self.objects.items() if obj.type == "SAT"}

    @property
    def debris(self) -> dict[str, "SpaceObject"]:
        return {id: obj for id, obj in self.objects.items() if obj.type == "DEBRIS"}

    def update_satellite(self, satellite_id, position, velocity,
                         timestamp=None, fuel_kg=None):
        if satellite_id in self.objects:
            obj = self.objects[satellite_id]
            obj.r = position
            obj.v = velocity
            if fuel_kg is not None:
                obj.fuel_kg = fuel_kg
        else:
            self.objects[satellite_id] = SpaceObject(
                id=satellite_id, type="SAT",
                r=position, v=velocity,
                fuel_kg=fuel_kg if fuel_kg is not None else INITIAL_FUEL_KG,
            )
        save_state()

    def update_debris(self, debris_id, position, velocity, timestamp=None):
        if debris_id in self.objects:
            obj = self.objects[debris_id]
            obj.r = position
            obj.v = velocity
        else:
            self.objects[debris_id] = SpaceObject(
                id=debris_id, type="DEBRIS",
                r=position, v=velocity, fuel_kg=0.0,
            )
        save_state()

    def add_maneuver(self, satellite_id, burn_cmd):
        dv = burn_cmd.deltaV_vector
        scheduled = ScheduledBurn(
            burn_id=burn_cmd.burn_id,
            satellite_id=satellite_id,
            burn_time_iso=burn_cmd.burnTime,
            burn_time_epoch=self._iso_to_epoch(burn_cmd.burnTime),
            delta_v_eci=[dv.x, dv.y, dv.z],
        )
        self.burns.append(scheduled)
        self.burns.sort(key=lambda b: b.burn_time_epoch)

    @staticmethod
    def _iso_to_epoch(iso_str: str) -> float:
        from datetime import timezone
        J2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        try:
            if iso_str.endswith("Z"):
                iso_str = iso_str[:-1] + "+00:00"
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt - J2000).total_seconds()
        except Exception:
            return 0.0


state = SimulationState()
state_store = state

import json, os

SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim_state.json")


def save_state():
    if not state.objects and os.path.exists(SAVE_FILE):
        return
    data = {
        "sim_time":  state.sim_time,
        "sim_epoch": state.sim_epoch,
        "objects": {
            id: {
                "id": obj.id, "type": obj.type,
                "r": obj.r, "v": obj.v,
                "fuel_kg": obj.fuel_kg, "status": obj.status,
                "nominal_r": obj.nominal_r, "nominal_v": obj.nominal_v,
            }
            for id, obj in state.objects.items()
        }
    }
    with open(SAVE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_state():
    if not os.path.exists(SAVE_FILE):
        return
    try:
        with open(SAVE_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    loaded_objects = data.get("objects", {})
    if not loaded_objects:
        return
    state.sim_time  = data.get("sim_time")
    state.sim_epoch = data.get("sim_epoch", 0.0)
    for id, obj in loaded_objects.items():
        state.objects[id] = SpaceObject(
            id=obj["id"], type=obj["type"],
            r=obj["r"], v=obj["v"],
            fuel_kg=obj.get("fuel_kg", INITIAL_FUEL_KG),
            status=obj.get("status", "NOMINAL"),
            nominal_r=obj.get("nominal_r"),
            nominal_v=obj.get("nominal_v"),
        )


def clear_save_file():
    if os.path.exists(SAVE_FILE):
        os.remove(SAVE_FILE)


load_state()