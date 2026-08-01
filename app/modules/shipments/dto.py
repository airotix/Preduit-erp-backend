"""Shipments API contracts."""
from pydantic import BaseModel, Field


class ShipmentCreate(BaseModel):
    order: str = Field(min_length=1, max_length=40)
    carrier: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=160)


class CarrierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    service: str = Field(min_length=1, max_length=80)
    avgTransit: str = Field(min_length=1, max_length=40)


class CarrierUpdate(CarrierCreate):
    """Same editable field set as create."""


class StatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=24)
