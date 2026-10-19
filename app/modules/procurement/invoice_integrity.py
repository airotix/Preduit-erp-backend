"""Validate invoice quantities/prices and calculate monetary fields on the server."""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from fastapi import HTTPException
from sqlalchemy import select
from app.models.procurement import PurchaseOrder
from app.models.finance import SupplierBill


def number(value):
    try:
        result = Decimal(str(value if value is not None else 0))
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except (ValueError, TypeError, InvalidOperation):
        raise HTTPException(422, "Invoice quantities and prices must be finite, non-negative numbers")


def cents(value):
    return number(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def signature(data):
    result = defaultdict(int)
    try:
        for article in data.get("articles") or []:
            name = str(article.get("style") or article.get("description") or "").strip()
            for row in article.get("rows") or []:
                price = number(row.get("unitPrice"))
                color = str(row.get("color") or "—").strip()
                for size, raw in (row.get("qty") or {}).items():
                    qty = number(raw)
                    if qty != qty.to_integral_value():
                        raise HTTPException(422, "Invoice quantities must be whole units")
                    if qty:
                        result[(name, color, str(size).strip(), price)] += int(qty)
    except (AttributeError, TypeError):
        raise HTTPException(422, "Enter a valid invoice article and size breakdown")
    return dict(result)


def source(session, tid, reference, lock=False):
    query = select(PurchaseOrder).where(PurchaseOrder.tenant_id == tid,
        PurchaseOrder.po_no == reference, PurchaseOrder.is_deleted.is_(False))
    return session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


def presentation_articles(expected, submitted):
    extras = {a.get("style"): a for a in submitted or []}
    result = deepcopy(expected)
    for article in result:
        original = extras.get(article["style"]) or {}
        for key in ("articleNo", "description", "fabric", "hsCode", "image"):
            if key in original:
                article[key] = original[key]
    return result


def bill_issue(session, invoice, expected_total, currency, supplier, lock=False):
    if not invoice or not invoice.supplier_bill_id:
        return None
    query = select(SupplierBill).where(SupplierBill.tenant_id == invoice.tenant_id,
        SupplierBill.id == invoice.supplier_bill_id, SupplierBill.is_deleted.is_(False))
    bill = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if bill is None:
        return "The linked Finance payable is missing. Reconcile it before saving this invoice."
    if cents(bill.amount) != cents(expected_total) or bill.po_ref != invoice.po_no or bill.supplier_name != supplier:
        return "The PO invoice and linked Finance payable do not agree. Reconcile the payable in Finance before saving; posted entries are not changed automatically."
    return None


def prepare(session, tid, data, invoice=None, lock=False):
    """Validate client line values, then replace all derived values with source values."""
    from app.modules.procurement import service
    reference = data.get("poNo")
    if invoice and reference != invoice.po_no:
        raise HTTPException(409, "An existing invoice cannot be moved to another PO")
    if not reference:
        # Historical standalone invoices retain their financial snapshot once
        # linked to a payable; no new standalone purchasing commitments here.
        if not invoice:
            raise HTTPException(422, "Choose the source PO before generating its invoice")
        previous = json.loads(invoice.data)
        if signature(data) != signature(previous) or any(data.get(k) != previous.get(k) for k in ("currency", "totals")):
            raise HTTPException(409, "Legacy invoice financial values require reconciliation before they can change")
        return deepcopy(data)
    po = source(session, tid, reference, lock)
    if po is None:
        raise HTTPException(409, "The source PO is unavailable; reconcile its invoice before saving")
    expected = service._build_invoice_draft(session, po_no=po.po_no)
    if signature(data) != signature(expected):
        raise HTTPException(409, "Invoice quantities and unit prices must match the saved PO. Resolve supplier differences through a PO revision or financial adjustment; changing the breakdown alone is not allowed.")
    if data.get("currency") != po.currency_code or (data.get("buyer") or {}).get("name") != po.supplier_name:
        raise HTTPException(409, "Invoice supplier and currency must match the source PO")
    if number((data.get("totals") or {}).get("freight")) != 0:
        raise HTTPException(409, "Freight cannot be added independently to a PO-linked invoice; reconcile the purchasing commitment first")
    issue = bill_issue(session, invoice, expected["totals"]["total"], po.currency_code, po.supplier_name, lock)
    if invoice and invoice.currency_code != po.currency_code:
        issue = "The saved invoice currency differs from its PO. Reconcile the currency and Finance records before correcting this document."
    if issue:
        raise HTTPException(409, issue)
    result = deepcopy(data)
    result["articles"] = presentation_articles(expected["articles"], data.get("articles"))
    result["totals"], result["currency"] = expected["totals"], expected["currency"]
    result["buyer"] = {**(result.get("buyer") or {}), "name": po.supplier_name}
    result["_financialLocked"] = True
    result.pop("_reconciliationRequired", None)
    return result


def display(session, invoice, data):
    """Refresh a consistent document; flag historical amount mismatches without posting."""
    from app.modules.procurement import service
    result = deepcopy(data)
    result["_financialLocked"] = bool(invoice.po_no or invoice.supplier_bill_id)
    if not invoice.po_no:
        return result
    po = source(session, invoice.tenant_id, invoice.po_no)
    if po is None:
        result["_reconciliationRequired"] = "Source PO unavailable. Reconcile this document before saving."
        return result
    expected = service._build_invoice_draft(session, po_no=po.po_no)
    issue = bill_issue(session, invoice, expected["totals"]["total"], po.currency_code, po.supplier_name)
    if invoice.currency_code != po.currency_code:
        issue = "This historical invoice's currency differs from the saved PO. Reconcile its Finance entry before correcting the document."
    if issue:
        result["_reconciliationRequired"] = issue
    else:
        result["articles"] = presentation_articles(expected["articles"], data.get("articles"))
        result["totals"], result["currency"] = expected["totals"], expected["currency"]
        result["buyer"] = {**(result.get("buyer") or {}), "name": po.supplier_name}
    return result
