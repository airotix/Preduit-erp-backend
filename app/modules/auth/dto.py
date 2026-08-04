"""Auth request bodies."""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=1)


class RegisterCompanyRequest(BaseModel):
    companyName: str = Field(min_length=1, max_length=200)
    ownerName: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=8, max_length=200)
    currency: str = "EUR"


class RefreshRequest(BaseModel):
    refreshToken: str


class LogoutRequest(BaseModel):
    refreshToken: str | None = None


class EmailOnlyRequest(BaseModel):
    email: str = Field(min_length=3, max_length=256)


class VerifyEmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    code: str = Field(min_length=4, max_length=10)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=256)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=8)
    password: str = Field(min_length=8, max_length=200)


class CreateInvitationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    role: str = Field(min_length=1, max_length=60)


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=8)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class UpdateUserRequest(BaseModel):
    role: str | None = Field(default=None, max_length=60)
    isActive: bool | None = None


class SetupInvite(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    role: str = Field(min_length=1, max_length=60)


class CompanySetupRequest(BaseModel):
    """Payload for the post-signup company setup wizard (Outlets/Modules/Team)."""
    companyName: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    taxRegistration: str | None = Field(default=None, max_length=60)
    modules: list[str] = Field(default_factory=list)
    invites: list[SetupInvite] = Field(default_factory=list)
