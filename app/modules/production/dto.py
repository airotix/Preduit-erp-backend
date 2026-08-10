"""Production API contracts."""
from decimal import Decimal

from pydantic import BaseModel, Field


class ProductionOrderCreate(BaseModel):
    style: str = Field(min_length=1, max_length=200)
    factory: str = Field(min_length=1, max_length=120)
    qty: int = Field(gt=0)


class BomCreate(BaseModel):
    component: str = Field(min_length=1, max_length=200)
    style: str = Field(min_length=1, max_length=200)
    material: str = Field(min_length=1, max_length=80)
    cost: Decimal = Field(gt=0)


class BomUpdate(BomCreate):
    """Same editable field set as create."""


class StatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=24)


class StageDaysIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    days: int = Field(ge=0)


class StartProductionIn(BaseModel):
    stages: list[StageDaysIn] = Field(min_length=1)
    # When set, only this production line's timeline is started (others keep theirs).
    line_id: str | None = Field(default=None, max_length=64)


class StageExtendIn(BaseModel):
    days: int = Field(gt=0)


class StageAssignIn(BaseModel):
    worker: str = Field(min_length=1, max_length=120)


class StageNotesIn(BaseModel):
    notes: str = Field(default="", max_length=400)


class ShipOrderIn(BaseModel):
    carrier: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=160)
    eta: str | None = Field(default=None, max_length=40)
