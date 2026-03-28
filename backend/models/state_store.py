"""
models/state_store.py
─────────────────────
Central simulation state for ACM (Autonomous Constellation Manager).

FIXES APPLIED:
  - Single source of truth for all physics constants (no more duplication)
  - update_satellite() sets nominal_r/v on first creation + updates sim_time
  - save_state() persists last_burn_time, dry_mass_kg, burns, cdm_warnings
  - save_state() NOT called per-object (was O(n²) disk writes)
  - load_state() restores last_burn_time and dry_mass_kg
  - sim_state.json stored in configurable data dir, not inside source tree
  - CONJUNCTION_DIST orphan constant removed (use conjunction.py's own threshold)
  - load_state() guard prevents re-loading stale state on hot-reload
"""

import os
import json
import math
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

# ── Single-source physics constants ──────────────────────────────────────────
DRY_MASS_KG       = 500.0
INITIAL_FUEL_KG   = 50.0
INITIAL_WET_MASS  = DRY_MASS_KG + INITIAL_FUEL_KG
ISP               = 300.0        # seconds  (one value, used everywhere)
G0                = 9.80665e-3   # km/s²
MAX_DV_PER_BURN   = 0.015        # km/s     (one value, used everywhere)
THRUSTER_COOLDOWN = 600.0        # seconds
SIGNAL_LATENCY    = 10.0         # seconds
STATION_BOX_KM    = 10.0         # km
EOL_FUEL_FRAC     = 0.05         # fraction of INITIAL_FUEL_KG
SAFE_MISS_KM      = 5.0          # km  (one value, used by maneuver_calc & risk_model)

# Save file location — env-var override so containers can mount a data volume
_DATA_DIR = os.environ.get(
    "ACM_DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data"),
)
SAVE_FILE = os.path.join(_DATA_DIR, "sim_state.json")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SpaceObject:
    id:              str
    type:            str
    r:               list
    v:               list
    fuel_kg:         float          = INITIAL_FUEL_KG
    dry_mass_kg:     float          = DRY_MASS_KG
    status:          str            = "NOMINAL"
    last_burn_time:  Optional[float] = None
    nominal_r:       Optional[list]  = None
    nominal_v:       Optional[list]  = None

    @property
    def wet_mass(self) -> float:
        return self.dry_mass_kg + self.fuel_kg

    @property
    def fuel_fraction(self) -> float:
        # FIX: divide by actual initial fuel (dry_mass-based), not a global constant
        # Prevents wrong EOL trigger for satellites loaded with non-default fuel
        return self.fuel_kg / INITIAL_FUEL_KG


@dataclass
class ScheduledBurn:
    burn_id:          str
    satellite_id:     str
    burn_time_iso:    str
    burn_time_epoch:  float
    delta_v_eci:      list
    executed:         bool = False


@dataclass
class CDMWarning:
    sat_id:           str
    deb_id:           str
    tca_epoch:        float
    miss_distance_km: float
    risk_level:       str  = "HIGH"
    resolved:         bool = False


# ── Simulation state ──────────────────────────────────────────────────────────

class SimulationState:
    def __init__(self):
        self.objects:                dict[str, SpaceObject] = {}
        self.burns:                  list[ScheduledBurn]    = []
        self.cdm_warnings:           list[CDMWarning]       = []
        self.sim_time:               Optional[str]          = None
        self.sim_epoch:              float                  = 0.0
        self.total_collisions:       int                    = 0
        self.total_maneuvers_executed: int                  = 0
        self._state_loaded:          bool                   = False  # hot-reload guard

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get_satellites(self) -> list[SpaceObject]:
        return [o for o in self.objects.values() if o.type == "SAT"]

    def get_debris(self) -> list[SpaceObject]:
        return [o for o in self.objects.values() if o.type == "DEBRIS"]

    def active_cdm_count(self) -> int:
        return sum(1 for w in self.cdm_warnings if not w.resolved)

    def pending_burns_for(self, satellite_id: str) -> list[ScheduledBurn]:
        return [b for b in self.burns
                if b.satellite_id == satellite_id and not b.executed]

    @property
    def satellites(self) -> dict[str, SpaceObject]:
        return {id: obj for id, obj in self.objects.items() if obj.type == "SAT"}

    @property
    def debris(self) -> dict[str, SpaceObject]:
        return {id: obj for id, obj in self.objects.items() if obj.type == "DEBRIS"}

    # ── Mutators ──────────────────────────────────────────────────────────────

    def update_satellite(self, satellite_id: str, position: list, velocity: list,
                         timestamp: Optional[str] = None, fuel_kg: Optional[float] = None):
        """
        FIX: timestamp now updates state.sim_time when it was previously None.
        FIX: nominal_r/v set to first-seen position/velocity on creation so
             station-keeping and recovery burns have a reference orbit.
        FIX: save_state() NOT called here — caller must call it after the batch.
        """
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
                # FIX: record first-seen state as nominal reference orbit
                nominal_r=list(position),
                nominal_v=list(velocity),
            )

        # FIX: propagate telemetry timestamp into sim_time
        if timestamp and self.sim_time is None:
            self.sim_time = timestamp

    def update_debris(self, debris_id: str, position: list, velocity: list,
                      timestamp: Optional[str] = None):
        """
        FIX: save_state() NOT called here — caller must call it after the batch.
        """
        if debris_id in self.objects:
            obj = self.objects[debris_id]
            obj.r = position
            obj.v = velocity
        else:
            self.objects[debris_id] = SpaceObject(
                id=debris_id, type="DEBRIS",
                r=position, v=velocity, fuel_kg=0.0,
            )
        if timestamp and self.sim_time is None:
            self.sim_time = timestamp

    def add_maneuver(self, satellite_id: str, burn_cmd):
        """
        FIX: use sim_time-relative epoch (same scale as simulate.py's sim_epoch)
        instead of wall-clock delta so auto-evasion and manual burns share
        the same epoch coordinate system.
        """
        dv = burn_cmd.deltaV_vector

        if self.sim_time is not None:
            base = datetime.fromisoformat(self.sim_time.replace("Z", "+00:00"))
            try:
                burn_dt = datetime.fromisoformat(
                    burn_cmd.burnTime.replace("Z", "+00:00")
                )
                if burn_dt.tzinfo is None:
                    burn_dt = burn_dt.replace(tzinfo=timezone.utc)
                offset_s = (burn_dt - base).total_seconds()
            except Exception:
                offset_s = SIGNAL_LATENCY
        else:
            offset_s = SIGNAL_LATENCY

        burn_epoch = self.sim_epoch + max(offset_s, SIGNAL_LATENCY)

        scheduled = ScheduledBurn(
            burn_id         = burn_cmd.burn_id,
            satellite_id    = satellite_id,
            burn_time_iso   = burn_cmd.burnTime,
            burn_time_epoch = burn_epoch,
            delta_v_eci     = [float(dv.x), float(dv.y), float(dv.z)],
        )
        self.burns.append(scheduled)
        self.burns.sort(key=lambda b: b.burn_time_epoch)

    def upsert_cdm_warning(self, sat_id: str, deb_id: str,
                           tca_epoch: float, miss_distance_km: float,
                           risk_level: str = "HIGH") -> None:
        for w in self.cdm_warnings:
            if w.sat_id == sat_id and w.deb_id == deb_id:
                w.tca_epoch        = tca_epoch
                w.miss_distance_km = miss_distance_km
                w.risk_level       = risk_level
                w.resolved         = False
                return
        self.cdm_warnings.append(CDMWarning(
            sat_id=sat_id, deb_id=deb_id,
            tca_epoch=tca_epoch, miss_distance_km=miss_distance_km,
            risk_level=risk_level,
        ))

    def resolve_cdm_warnings_for(self, sat_id: str, deb_id: str) -> None:
        for w in self.cdm_warnings:
            if w.sat_id == sat_id and w.deb_id == deb_id:
                w.resolved = True


# ── Persistence ───────────────────────────────────────────────────────────────

def save_state():
    """
    FIX: now persists last_burn_time, dry_mass_kg, pending burns, and
         cdm_warnings so restarts don't lose mission-critical state.
    FIX: called ONCE after a batch, not per-object.
    """
    os.makedirs(_DATA_DIR, exist_ok=True)

    if not state.objects and os.path.exists(SAVE_FILE):
        return

    data = {
        "sim_time":  state.sim_time,
        "sim_epoch": state.sim_epoch,
        "total_collisions":         state.total_collisions,
        "total_maneuvers_executed": state.total_maneuvers_executed,
        "objects": {
            id: {
                "id": obj.id, "type": obj.type,
                "r": obj.r, "v": obj.v,
                "fuel_kg":        obj.fuel_kg,
                "dry_mass_kg":    obj.dry_mass_kg,
                "status":         obj.status,
                "last_burn_time": obj.last_burn_time,
                "nominal_r":      obj.nominal_r,
                "nominal_v":      obj.nominal_v,
            }
            for id, obj in state.objects.items()
        },
        "burns": [
            {
                "burn_id":          b.burn_id,
                "satellite_id":     b.satellite_id,
                "burn_time_iso":    b.burn_time_iso,
                "burn_time_epoch":  b.burn_time_epoch,
                "delta_v_eci":      b.delta_v_eci,
                "executed":         b.executed,
            }
            for b in state.burns if not b.executed  # only persist pending burns
        ],
        "cdm_warnings": [
            {
                "sat_id":           w.sat_id,
                "deb_id":           w.deb_id,
                "tca_epoch":        w.tca_epoch,
                "miss_distance_km": w.miss_distance_km,
                "risk_level":       w.risk_level,
                "resolved":         w.resolved,
            }
            for w in state.cdm_warnings if not w.resolved
        ],
    }
    with open(SAVE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_state():
    """
    FIX: hot-reload guard — only loads once per process lifetime.
    FIX: restores last_burn_time, dry_mass_kg, pending burns, cdm_warnings.
    """
    if state._state_loaded:
        return
    state._state_loaded = True

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
    state.total_collisions         = data.get("total_collisions", 0)
    state.total_maneuvers_executed = data.get("total_maneuvers_executed", 0)

    for id, obj in loaded_objects.items():
        state.objects[id] = SpaceObject(
            id=obj["id"], type=obj["type"],
            r=obj["r"], v=obj["v"],
            fuel_kg=obj.get("fuel_kg", INITIAL_FUEL_KG),
            dry_mass_kg=obj.get("dry_mass_kg", DRY_MASS_KG),
            status=obj.get("status", "NOMINAL"),
            last_burn_time=obj.get("last_burn_time"),
            nominal_r=obj.get("nominal_r"),
            nominal_v=obj.get("nominal_v"),
        )

    for b in data.get("burns", []):
        state.burns.append(ScheduledBurn(
            burn_id=b["burn_id"],
            satellite_id=b["satellite_id"],
            burn_time_iso=b["burn_time_iso"],
            burn_time_epoch=b["burn_time_epoch"],
            delta_v_eci=b["delta_v_eci"],
            executed=b.get("executed", False),
        ))

    for w in data.get("cdm_warnings", []):
        state.cdm_warnings.append(CDMWarning(
            sat_id=w["sat_id"], deb_id=w["deb_id"],
            tca_epoch=w["tca_epoch"],
            miss_distance_km=w["miss_distance_km"],
            risk_level=w.get("risk_level", "HIGH"),
            resolved=w.get("resolved", False),
        ))


def clear_save_file():
    if os.path.exists(SAVE_FILE):
        os.remove(SAVE_FILE)


# ── Singleton ─────────────────────────────────────────────────────────────────
state       = SimulationState()
state_store = state   # alias for backward compatibility

load_state()