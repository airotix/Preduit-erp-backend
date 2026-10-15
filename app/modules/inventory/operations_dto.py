from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, Field, model_validator


class RuleInput(BaseModel):
    variantId: UUID
    locationId: UUID
    supplierId: UUID
    minimum: int = Field(ge=0)
    target: int = Field(gt=0)
    leadDays: int = Field(ge=0, le=3650)
    minimumOrder: int = Field(default=1, gt=0)
    packMultiple: int = Field(default=1, gt=0)
    active: bool = True
    @model_validator(mode="after")
    def levels(self):
        if self.target <= self.minimum:
            raise ValueError("Target must exceed minimum")
        return self


class ActionLine(BaseModel):
    id: int
    quantity: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    repair: int = Field(default=0, ge=0)
    scrap: int = Field(default=0, ge=0)


class Action(BaseModel):
    requestId: UUID
    reason: str = Field(min_length=3, max_length=400)
    override: bool = False
    locationId: UUID | None = None
    counter: str | None = Field(default=None, max_length=256)
    lines: list[ActionLine] = Field(default_factory=list, max_length=500)


class CountCreate(BaseModel):
    requestId: UUID
    locationId: UUID
    reference: str = Field(min_length=3, max_length=80)
    reason: str = Field(min_length=3, max_length=400)
    counter: str = Field(min_length=3, max_length=256)
    variantIds: list[UUID] = Field(default_factory=list, max_length=500)


class ReturnRequestLine(BaseModel):
    orderLineId: int
    quantity: int = Field(gt=0)


class ReturnCreate(BaseModel):
    requestId: UUID
    orderId: UUID
    locationId: UUID
    reason: str = Field(min_length=3, max_length=80)
    refund: Decimal = Field(default=0, ge=0)
    lines: list[ReturnRequestLine] = Field(min_length=1, max_length=500)
