"""Admin API contracts."""
from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    role: str = Field(min_length=1, max_length=60)
    department: str = Field(min_length=1, max_length=120)


class UserUpdate(UserCreate):
    """Same editable field set as create."""


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: str = Field(min_length=1, max_length=200)


class RoleUpdate(RoleCreate):
    """Same editable field set as create."""


class ApprovalRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    condition: str = Field(min_length=1, max_length=300)
    approver: str = Field(min_length=1, max_length=200)


class ApprovalRuleUpdate(ApprovalRuleCreate):
    """Same editable field set as create."""
