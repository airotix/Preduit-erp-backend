"""Quality API contracts."""
from pydantic import BaseModel, Field


class InspectionCreate(BaseModel):
    order: str = Field(min_length=1, max_length=40)
    stage: str = Field(pattern="^(Inline|Final)$")
    aql: str = Field(pattern="^(2\\.5|4\\.0)$")


class DefectTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(pattern="^(Stitching|Fabric|Trim)$")
    severity: str = Field(pattern="^(Major|Minor)$")


class DefectTypeUpdate(DefectTypeCreate):
    """Same editable field set as create."""


class StatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=24)


class PassShipIn(BaseModel):
    """Mark an inspection Passed and ship it (carrier + destination)."""
    carrier: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=160)
