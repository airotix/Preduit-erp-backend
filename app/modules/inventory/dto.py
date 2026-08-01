"""Inventory API contracts (Pydantic v2) — match the frontend forms."""
from pydantic import BaseModel, Field


class StockReceiptCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    location: str = Field(min_length=1, max_length=120)
    onHand: int
    reserved: int = 0


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=40)
    type: str = Field(pattern="^(Warehouse|Retail)$")
    region: str = Field(min_length=1, max_length=80)
    capacity: int | None = None


class LocationUpdate(LocationCreate):
    """Same editable field set as create."""


class TransferCreate(BaseModel):
    from_: str = Field(min_length=1, alias="from")
    to: str = Field(min_length=1)
    units: int = Field(gt=0)


class ReorderAlertCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    suggested: int = Field(gt=0)
    supplier: str = Field(min_length=1, max_length=200)


class StatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=24)


# ---- Stock matrix editor (color × size grid on the article drill-down) ----

class MatrixCellIn(BaseModel):
    size: str = Field(min_length=1, max_length=60)
    qty: int = Field(ge=0)


class MatrixColorIn(BaseModel):
    color: str = Field(min_length=1, max_length=60)
    hex: str | None = Field(default=None, max_length=9)
    cells: list[MatrixCellIn] = Field(default_factory=list)


class MatrixUpdate(BaseModel):
    colors: list[MatrixColorIn] = Field(default_factory=list)
