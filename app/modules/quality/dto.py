"""Quality API contracts."""
from pydantic import BaseModel, Field


class InspectionCreate(BaseModel):
    order: str = Field(min_length=1, max_length=40)
    # Widened beyond Inline/Final — a stage label from the production timeline.
    stage: str = Field(min_length=1, max_length=20)
    aql: str = Field(default="2.5", max_length=10)
    # Optional header fields; anything omitted is auto-pulled from the PO.
    sku: str | None = Field(default=None, max_length=64)
    product: str | None = Field(default=None, max_length=200)
    batchLot: str | None = Field(default=None, max_length=60)
    inspectionType: str | None = Field(default=None, max_length=40)
    inspector: str | None = Field(default=None, max_length=120)
    # Optional item (production line) this inspection is for; blank = whole order.
    item: str | None = Field(default=None, max_length=200)


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
    qtyAffected: int = Field(default=1, ge=1)
    location: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=400)
    corrective: str | None = Field(default=None, max_length=400)
    imageDocId: str | None = Field(default=None, max_length=64)


class InspectionHeaderUpdate(BaseModel):
    """Edit the inspection header (from the drill-down Edit button)."""
    inspector: str | None = Field(default=None, max_length=120)
    batchLot: str | None = Field(default=None, max_length=60)
    inspectionType: str | None = Field(default=None, max_length=40)
    aql: str | None = Field(default=None, max_length=10)
    # Optional manual override of the sample size (string; blank = keep/auto).
    sampleSize: str | None = Field(default=None, max_length=10)


class DispositionIn(BaseModel):
    """Disposition for a FAILED inspection."""
    disposition: str = Field(pattern="^(Rework|Hold|Scrap|Return to Vendor|Re-inspection)$")
    notes: str | None = Field(default=None, max_length=400)
    assignedTo: str | None = Field(default=None, max_length=120)
    dueDate: str | None = Field(default=None, max_length=20)   # ISO date string


class StatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=24)


class PassShipIn(BaseModel):
    """Mark an inspection Passed and ship it (carrier + destination)."""
    carrier: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=160)
