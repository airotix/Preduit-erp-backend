"""AI Insights request bodies."""
from pydantic import BaseModel


class SizeQty(BaseModel):
    size: str
    finalQty: int


class RecommendationOverride(BaseModel):
    sizeBreakdown: list[SizeQty]
    reason: str | None = None
    notes: str | None = None


class RevertPayload(BaseModel):
    reference: str
    couleur: str | None = None
    taille: str | None = None


class SeasonConfig(BaseModel):
    name: str = ""
    currency: str = "EUR"
    budget: float = 0
    targetMargin: float = 50
    budgetBandPct: float = 15
    costRatio: float = 50
