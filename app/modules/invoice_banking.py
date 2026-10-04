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
