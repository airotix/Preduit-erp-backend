from pydantic import BaseModel, Field


class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    base_currency_code: str = Field(min_length=3, max_length=3)


class CreateOrgResponse(BaseModel):
    tenant_id: str
    slug: str
    owner_email: str | None
