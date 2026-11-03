"""Quality API contracts."""
from pydantic import BaseModel, Field
from typing import Literal


class ReopenIn(BaseModel):
    mode: Literal["inspection", "production"]
    notes: str | None = Field(default=None, max_length=400)
    assignedTo: str | None = Field(default=None, max_length=120)
    dueDate: str | None = Field(default=None, max_length=20)


class DefectTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=40)
    severity: str = Field(pattern="^(Critical|Major|Minor)$")


class DefectTypeUpdate(DefectTypeCreate):
    """Same editable field set as create."""


class CheckIn(BaseModel):
    """A checklist criterion result."""
    criterion: str = Field(min_length=1, max_length=120)
    requirement: str | None = Field(default=None, max_length=200)
    targetValue: float | None = None
    tolerance: float | None = None
    actual: str | None = Field(default=None, max_length=120)
    result: str = Field(default="Pending", pattern="^(Pass|Fail|Warning|NA|Pending)$")
    notes: str | None = Field(default=None, max_length=400)


class DefectIn(BaseModel):
    """A defect logged against an inspection (from the Defect Types catalog)."""
    defectTypeId: str | None = Field(default=None, max_length=64)   # public_id of a DefectType
    defectName: str | None = Field(default=None, max_length=120)    # free-text fallback
    newDefect: DefectTypeCreate | None = None  # create and log together from the inspection
    qtyAffected: int = Field(default=1, ge=1)
    location: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=400)
    corrective: str | None = Field(default=None, max_length=400)
    imageDocId: str | None = Field(default=None, max_length=64)


class QualitySettingsIn(BaseModel):
    """Company-wide quality settings."""
    aql: Literal["1.0", "1.5", "2.5", "4.0", "6.5"]


class QcQtyLine(BaseModel):
    sourceId: int
    qty: int = Field(ge=0)


class StartTask(BaseModel):
    taskKey: str = Field(min_length=3)
    requestId: str = Field(min_length=8)
    stage: Literal["Final", "Inline"]
    purpose: Literal["awaiting", "held"] = "awaiting"
    samplingMode: Literal["sample", "full"]
    sampleSize: int | None = None
    inspector: str | None = Field(default=None, max_length=120)
    lines: list[QcQtyLine] = []


class QcSplitLine(BaseModel):
    sourceId: int
    approved: int = Field(ge=0)
    held: int = Field(ge=0)
    rejected: int = Field(ge=0)


class DecideTask(BaseModel):
    taskKey: str
    requestId: str
    decision: Literal["approve_all", "hold_all", "split", "inline"]
    exceptionReason: str | None = Field(default=None, max_length=400)
    allocations: list[QcSplitLine] = []


class ResolveTask(BaseModel):
    taskKey: str
    requestId: str
    action: Literal["reinspect", "rework", "reject"]
    reason: str = Field(default="", max_length=400)
    lines: list[QcQtyLine] = []
