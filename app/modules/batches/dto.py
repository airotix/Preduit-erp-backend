from datetime import date
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict, model_validator


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    reason: str = Field(min_length=1, max_length=400)


class PlanLine(BaseModel):
    sourceId: int = Field(gt=0)
    qty: int = Field(gt=0, strict=True)


class CreateBatch(Command):
    name: str = Field(min_length=1, max_length=120)
    factory: str = Field(default="", max_length=120)
    dueOn: date | None = None
    lines: list[PlanLine] = Field(min_length=1)
    stages: list[str] = Field(min_length=1, max_length=30)


class Quantity(Command):
    qty: int = Field(gt=0, strict=True)
    locationId: int | None = None


class QC(Command):
    kind: str = Field(pattern="^(Inline|Final|Reinspection)$")
    assessed: int = Field(gt=0, strict=True)
    sampleSize: int = Field(gt=0, strict=True)
    approved: int = Field(ge=0, strict=True)
    held: int = Field(ge=0, strict=True)
    rejected: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def balance(self):
        if self.approved + self.held + self.rejected != self.assessed:
            raise ValueError("Approved, held and rejected must total the assessed lot quantity.")
        if self.sampleSize > self.assessed:
            raise ValueError("Sample size cannot exceed assessed quantity.")
        return self


class StageCommand(Command):
    status: str = Field(pattern="^(In progress|Completed)$")
    worker: str = Field(default="", max_length=120)


class AllocationIn(BaseModel):
    lotId: int = Field(gt=0)
    qty: int = Field(gt=0, strict=True)


class InventoryIn(BaseModel):
    reservationId: int = Field(gt=0)
    qty: int = Field(gt=0, strict=True)


class CreateShipment(Command):
    carrier: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=160)
    eta: str = Field(default="", max_length=40)
    allocations: list[AllocationIn] = Field(default_factory=list)
    inventoryAllocations: list[InventoryIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_stock(self):
        if not self.allocations and not self.inventoryAllocations:
            raise ValueError("Allocate at least one source quantity")
        return self


class ShipmentStatus(Command):
    status: str = Field(pattern="^(In transit|Customs|Out for delivery|Delivered|Cancelled|Returned)$")
