"""
Pydantic schemas — hackathon API specification.
"""
from pydantic import BaseModel, field_validator
from typing import Optional


class Vec3(BaseModel):
    x: float
    y: float
    z: float

    def to_list(self) -> list:
        return [self.x, self.y, self.z]


class TelemetryObject(BaseModel):
    id: str
    type: str
    r: Vec3
    v: Vec3

    @field_validator("type")
    @classmethod
    def type_must_be_valid(cls, v):
        if v not in ("SAT", "DEBRIS"):
            raise ValueError("type must be SAT or DEBRIS")
        return v


class TelemetryRequest(BaseModel):
    timestamp: str
    objects: list[TelemetryObject]


class TelemetryResponse(BaseModel):
    status: str = "ACK"
    processed_count: int
    active_cdm_warnings: int


class BurnCommand(BaseModel):
    burn_id: str
    burnTime: str
    deltaV_vector: Vec3


class ManeuverRequest(BaseModel):
    satelliteId: str
    maneuver_sequence: list[BurnCommand]


class ManeuverValidation(BaseModel):
    ground_station_los: bool
    sufficient_fuel: bool
    projected_mass_remaining_kg: float


class ManeuverResponse(BaseModel):
    status: str
    validation: ManeuverValidation
    reject_reason: str = ""    # populated when status="REJECTED", empty otherwise


class SimStepRequest(BaseModel):
    step_seconds: float

    @field_validator("step_seconds")
    @classmethod
    def must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("step_seconds must be positive")
        return v


class SimStepResponse(BaseModel):
    status: str = "STEP_COMPLETE"
    new_timestamp: str
    collisions_detected: int
    maneuvers_executed: int


class SatelliteSnapshot(BaseModel):
    id: str
    x: float
    y: float
    z: float
    fuel_kg: float
    status: str
    # pending_burns lets the frontend colour a satellite YELLOW as soon as
    # a burn is queued, without waiting for the status field to update
    pending_burns: int = 0


class DebrisSnapshot(BaseModel):
    id: str
    x: float
    y: float
    z: float


class VisualizationSnapshot(BaseModel):
    timestamp:  str
    satellites: list[SatelliteSnapshot]
    debris:     list[DebrisSnapshot]