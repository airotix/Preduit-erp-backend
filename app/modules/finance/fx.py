"""Exchange-rate sync for the ERP.

Populates the dated dbo.exchange_rates table from the free, key-less
Frankfurter/ECB API so the currency conversion stays current. (The transactional
FX gain/loss module was removed per client request; only rate management remains.)
"""
import datetime

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.modules.finance import repository as repo

settings = get_settings()


def _iso(s) -> datetime.date:
    if isinstance(s, datetime.date):
        return s
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return datetime.date.today()


def rates_screen(session) -> dict:
    base = repo.base_currency(session) or "EUR"
    rows = repo.list_rates(session)
    return {
        "base": base,
        "rates": [
            {"from": r["from_ccy"], "to": r["to_ccy"], "rate": float(r["rate"]),
             "validFrom": r["valid_from"].isoformat() if r["valid_from"] else None,
             "source": r["source"]}
            for r in rows
        ],
    }


# Currencies we keep rates for (paired against the base, both directions).
_DEFAULT_SYMBOLS = ["USD", "EUR", "GBP", "PKR", "AED", "SAR", "INR",
                    "CNY", "JPY", "CHF", "CAD", "AUD"]


def sync_rates(session, tenant_id) -> dict:
    """Pull daily rates from ExchangeRate-API into the dated exchange_rates table
    (both base→ccy and ccy→base). Endpoint: {url}/{key}/latest/{BASE}."""
    base = repo.base_currency(session) or "EUR"
    if not settings.fx_api_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "No FX API key configured. Add FX_API_KEY to the backend .env "
                            "(free key from exchangerate-api.com).")
    url = f"{settings.fx_provider_url}/{settings.fx_api_key}/latest/{base}"
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Rate provider unavailable ({exc}).") from exc

    if data.get("result") != "success":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Rate provider error: {data.get('error-type', 'unknown')}.")

    conv = data.get("conversion_rates") or {}
    ts = data.get("time_last_update_unix")
    valid_from = datetime.datetime.utcfromtimestamp(ts).date() if ts else datetime.date.today()

    # Only store rates for currencies that exist in dbo.currencies — from_ccy/to_ccy
    # are FKs to it, so unknown codes would fail the insert.
    known = repo.currency_codes(session)
    symbols = [c for c in known if c != base] or [c for c in _DEFAULT_SYMBOLS if c != base]

    n = 0
    try:
        for ccy in symbols:
            rate = conv.get(ccy)
            if not rate or float(rate) <= 0:
                continue
            rate = float(rate)
            repo.upsert_rate(session, tenant_id=tenant_id, from_ccy=base, to_ccy=ccy,
                             rate=round(rate, 8), valid_from=valid_from, source="exchangerate-api")
            repo.upsert_rate(session, tenant_id=tenant_id, from_ccy=ccy, to_ccy=base,
                             rate=round(1.0 / rate, 8), valid_from=valid_from, source="exchangerate-api")
            n += 2
        session.flush()
    except Exception as exc:  # surface as a clean error (keeps CORS headers)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"Could not save rates: {exc}") from exc
    return {"base": base, "date": valid_from.isoformat(), "pairs": n, "source": "exchangerate-api"}
