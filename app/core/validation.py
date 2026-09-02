"""Shared field-format validators for API DTOs (server-side enforcement that
mirrors the frontend checks). Empty/None passes — 'required' is enforced
separately by Field constraints."""
import re

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
PHONE_RE = re.compile(r"^\+?[\d\s().\-]{7,}$")
URL_RE = re.compile(r"^(https?://)?([\w-]+\.)+[\w-]{2,}(/\S*)?$", re.IGNORECASE)


def clean_email(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if not EMAIL_RE.match(s):
        raise ValueError("Enter a valid email address.")
    return s


def clean_phone(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    digits = sum(ch.isdigit() for ch in s)
    if not PHONE_RE.match(s) or not (7 <= digits <= 15):
        raise ValueError("Enter a valid phone number.")
    return s


def clean_url(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if not URL_RE.match(s):
        raise ValueError("Enter a valid URL.")
    return s
