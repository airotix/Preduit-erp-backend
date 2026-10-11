"""Fill missing invoice banking fields without replacing saved overrides."""

def fill_missing(saved, current):
    result = dict(saved or {})
    for key, value in current.items():
        if not str(result.get(key) or "").strip():
            result[key] = value or ""
    return result


def company_bank(company):
    return {"title": company.get("legal_name") or company.get("name") or "",
            "bank": company.get("bank_name") or "", "account": company.get("bank_account") or "",
            "iban": company.get("bank_iban") or "", "swift": company.get("bank_swift") or ""}


def supplier_bank(supplier, name):
    return {"beneficiary": (supplier.bank_account_title if supplier else "") or name or "",
            "bank": (supplier.bank_name if supplier else "") or (supplier.bank_details if supplier else "") or "",
            "account": (supplier.bank_account_number if supplier else "") or "",
            "iban": (supplier.bank_iban if supplier else "") or "",
            "swift": (supplier.bank_swift if supplier else "") or "", "branch": ""}


def profile_defaults(session, tenant_id, data, actor_id=None, *, sales=False):
    """Snapshot database profile defaults; retain invoice-specific overrides."""
    from uuid import UUID
    from sqlalchemy import select
    from app.models.core import Tenant, User
    tid = UUID(str(tenant_id))
    company = session.get(Tenant, tid)
    result = dict(data)
    if company:
        address = ", ".join(str(v) for v in
            (company.street, company.city, company.state, company.postal) if v)
        result["exporter"] = fill_missing(result.get("exporter"), {
            "name": company.legal_name or company.name, "address": address,
            "country": company.country, "tel": company.phone,
            "email": company.business_email, "taxId": company.tax_registration})
        bank = company_bank({k: getattr(company, k) for k in
            ("name", "legal_name", "bank_name", "bank_account", "bank_iban", "bank_swift")})
        key = "remit" if sales else "companyBank"
        result[key] = fill_missing(result.get(key), bank)
    # An automatic invoice has no human preparer. Never attribute it to an owner.
    if actor_id:
        user = session.scalar(select(User).where(User.tenant_id == tid,
            User.public_id == UUID(str(actor_id)), User.is_active.is_(True)))
        if user:
            result = fill_missing(result, {"preparedBy": user.display_name or user.email,
                "preparedByEmail": user.email})
    return result
