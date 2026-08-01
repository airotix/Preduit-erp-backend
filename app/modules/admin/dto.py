"""Admin API contracts."""
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# The only roles a user may be assigned. Keep in sync with the frontend
# userSchema enum and the onboarding _DEFAULT_ROLES set.
RoleName = Literal[
    "Admin", "Manager", "Merchandiser", "Accountant", "User Overview", "Logistics / Inventory",
]


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    role: RoleName
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
