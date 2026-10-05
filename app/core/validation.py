"""Shared field-format validators for API DTOs (server-side enforcement that
mirrors the frontend checks). Empty/None passes — 'required' is enforced
separately by Field constraints."""
import re
import phonenumbers
from app.core.locations import country_code, clean_location

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9\s().\-]{7,}$")
PHONE_MAX_DIGITS = 14
PHONE_ERROR = "Enter a valid phone number with its country code (up to 14 digits)."
URL_RE = re.compile(r"^(https?://)?([\w-]+\.)+[\w-]{2,}(/\S*)?$", re.IGNORECASE)


def clean_contact(contact):
    """Shared DTO hook: validate dependencies before country-aware phone parsing."""
    location = clean_location(contact.country, contact.state, contact.city)
    contact.country, contact.state, contact.city = location["country"], location["state"], location["city"]
    contact.phone = clean_phone(contact.phone, contact.country or contact.region)
    return contact


def clean_email(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if not EMAIL_RE.match(s):
        raise ValueError("Enter a valid email address.")
    return s


def clean_phone(v: str | None, country: str | None = None, *, mobile_only: bool = False) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if not PHONE_RE.fullmatch(s):
        raise ValueError(PHONE_ERROR)
    region = country_code(country)
    if not s.startswith("+") and not region:
        raise ValueError("Select a phone country or include + and the country code.")
    try:
        phone = phonenumbers.parse(s, region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(PHONE_ERROR) from exc
    normalized = phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)
    if len(normalized.lstrip("+")) > PHONE_MAX_DIGITS:
        raise ValueError("Phone numbers can contain up to 14 digits, including the country code.")
    if not phonenumbers.is_possible_number(phone):
        raise ValueError("This phone number has an incorrect length for its country.")
    if not phonenumbers.is_valid_number(phone):
        raise ValueError("Enter a valid phone number for the selected country.")
    if mobile_only and phonenumbers.number_type(phone) not in (
            phonenumbers.PhoneNumberType.MOBILE, phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE):
        raise ValueError("Enter a mobile number; landlines are not accepted in this field.")
    return normalized


def validate_invoice_phones(data: dict) -> None:
    """Enforce phone formats in invoice JSON before any database changes."""
    from fastapi import HTTPException
    try:
        if data.get("contactPhone"):
            data["contactPhone"] = clean_phone(data["contactPhone"], (data.get("buyer") or {}).get("country"))
        if data.get("origin"):
            data["origin"] = clean_location(data["origin"])["country"]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    for party in ("exporter", "buyer", "supplier", "seller"):
        fields = data.get(party) or {}
        country = fields.get("country")
        if country:
            try:
                location = clean_location(country, fields.get("state"), fields.get("city"))
                fields["country"] = location["country"]
                for key in ("state", "city"):
                    if key in fields:
                        fields[key] = location[key] or ""
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        for name, value in (data.get(party) or {}).items():
            if re.search(r"phone|mobile|tel\b|telephone|contactnumber|whatsapp|fax|supportline",
                         re.sub(r"[_\s-]", "", name.lower())):
                try:
                    fields[name] = clean_phone(value, country, mobile_only="mobile" in name.lower()) or ""
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc


def clean_url(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if not URL_RE.match(s):
        raise ValueError("Enter a valid URL.")
    return s
