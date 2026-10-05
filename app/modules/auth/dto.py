"""Auth request bodies."""
from pydantic import BaseModel, Field, field_validator, model_validator
from app.core.validation import clean_phone, PHONE_ERROR
from app.core.locations import clean_location


class LocationRequest(BaseModel):
    @model_validator(mode="after")
    def validate_location(self):
        cleaned = clean_location(self.country, city=self.city)
        self.country, self.city = cleaned["country"], cleaned["city"]
        return self


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=1)
    # When an email owns several businesses, the business name on the login form
    # selects which ERP to sign in to.
    businessName: str | None = Field(default=None, max_length=200)
    # "Keep me signed in" → a persistent refresh cookie; false → session cookie.
    remember: bool = True


class SwitchBusinessRequest(BaseModel):
    businessId: str = Field(min_length=1, max_length=64)


class RegisterCompanyRequest(BaseModel):
    companyName: str = Field(min_length=1, max_length=200)
    ownerName: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=8, max_length=200)
    currency: str = "EUR"


class RefreshRequest(BaseModel):
    # Optional now — the refresh token normally arrives as an HttpOnly cookie.
    refreshToken: str | None = None


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


class WorkspaceRequestForm(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contactNumber: str = Field(min_length=1, max_length=40)
    email: str = Field(min_length=3, max_length=256)
    businessName: str = Field(min_length=1, max_length=200)
    businessDescription: str = Field(default="", max_length=2000)

    @field_validator("contactNumber")
    @classmethod
    def _v_contact_number(cls, v):
        cleaned = clean_phone(v)
        if cleaned is None:
            raise ValueError(PHONE_ERROR)
        return cleaned


class CreateWorkspaceRequest(LocationRequest):
    """Super Admin provisioning: owner account + the full setup stepper payload
    (business, modules, team) in one call."""
    ownerName: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=256)
    # Ignored when the email already owns a workspace (existing credentials are reused).
    password: str = Field(min_length=8, max_length=200)
    companyName: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    taxRegistration: str | None = Field(default=None, max_length=60)
    modules: list[str] = Field(default_factory=list)
    invites: list[SetupInvite] = Field(default_factory=list)


class CompanySetupRequest(LocationRequest):
    """Payload for the post-signup company setup wizard (Outlets/Modules/Team)."""
    companyName: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    taxRegistration: str | None = Field(default=None, max_length=60)
    modules: list[str] = Field(default_factory=list)
    invites: list[SetupInvite] = Field(default_factory=list)
