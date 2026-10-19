"""Trace supplier returns from physical dispatch to money or replacement resolution."""
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from app.models.supplier_returns import SupplierReturn
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, GoodsReceipt, GoodsReceiptLine
from app.models.catalog import Product, ProductVariant
from app.models.inventory import StockLevel, StockEntry, Location
from app.models.batches import BatchLot, BatchLine, ProductionBatch
from app.models.finance import SupplierBill, JournalEntry, JournalLine
from app.models.quality import Inspection
from app.modules.inventory import workflows, ledger
from app.core.audit import write_audit


class ReturnIn(BaseModel):
    requestId: UUID
    poLineId: UUID
    locationId: UUID
    lotId: UUID | None = None
    legacyMovementId: UUID | None = None
    quantity: int = Field(gt=0)
    resolution: Literal['Credit', 'Refund', 'Replacement']
    reference: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=3, max_length=400)


class CreditIn(BaseModel):
    requestId: UUID
    billId: UUID
    reference: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=3, max_length=400)


class RefundIn(BaseModel):
    requestId: UUID
    channel: Literal['cash', 'bank']
    reference: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=3, max_length=400)


class ReceiveIn(BaseModel):
    requestId: UUID
    reference: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=3, max_length=400)


class ResolutionIn(ReceiveIn):
    resolution: Literal['Credit', 'Refund']


def money(value):
    return Decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def row(db, model, tid, pid, lock=False):
    q = select(model).where(model.tenant_id == tid, model.public_id == UUID(str(pid)))
    obj = db.scalar(q.with_for_update().execution_options(populate_existing=True) if lock else q)
    if obj is None:
        raise HTTPException(404, 'Record not found')
    return obj


def audit(db, ret, text):
    write_audit(db, tenant_id=ret.tenant_id, action='UPDATE', entity_type='supplier_return',
        entity_id=str(ret.public_id), detail=text)


def source_received(db, tid, line):
    batch_receipt = select(StockEntry.id).where(StockEntry.tenant_id == tid,
        StockEntry.source_module == 'procurement', StockEntry.source_type == 'receipt',
        StockEntry.source_id == GoodsReceipt.public_id, StockEntry.batch_id.is_not(None),
        StockEntry.kind == 'Receipt').exists()
    normal = db.scalar(select(func.coalesce(func.sum(GoodsReceiptLine.received), 0))
        .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.grn_id).where(
            GoodsReceiptLine.tenant_id == tid, GoodsReceipt.tenant_id == tid,
            GoodsReceipt.is_deleted.is_(False), GoodsReceiptLine.po_line_id == line.id, ~batch_receipt))
    batch = db.scalar(select(func.coalesce(func.sum(BatchLine.received), 0)).where(
        BatchLine.tenant_id == tid, BatchLine.po_line_id == line.id))
    outstanding = db.scalar(select(func.coalesce(func.sum(SupplierReturn.quantity), 0)).where(
        SupplierReturn.tenant_id == tid, SupplierReturn.po_line_id == line.id, SupplierReturn.replaced.is_(False)))
    return max(0, int(normal or 0) + int(batch or 0) - int(outstanding or 0))


def options(db, tid):
    lines = db.execute(select(PurchaseOrderLine, PurchaseOrder).join(PurchaseOrder,
        PurchaseOrder.id == PurchaseOrderLine.po_id).where(PurchaseOrder.tenant_id == tid,
        PurchaseOrderLine.tenant_id == tid, PurchaseOrderLine.variant_id.is_not(None))
        .order_by(PurchaseOrder.id.desc(), PurchaseOrderLine.id)).all()
    result = []
    for line, po in lines:
        available = source_received(db, tid, line)
        if available <= 0:
            continue
        lots = db.execute(select(BatchLot, BatchLine).join(BatchLine, BatchLine.id == BatchLot.line_id).where(
            BatchLot.tenant_id == tid, BatchLine.tenant_id == tid, BatchLine.po_line_id == line.id)).all()
        result.append({'id': str(line.public_id), 'po': po.po_no, 'supplier': po.supplier_name,
            'sku': line.sku, 'name': line.name, 'color': line.color, 'size': line.size,
            'price': str(line.price), 'returnable': available,
            'lots': [{'id': str(lot.public_id), 'locationId': lot.location_id,
                'onHand': lot.on_hand, 'allocated': lot.allocated} for lot, _ in lots]})
    locations = db.scalars(select(Location).where(Location.tenant_id == tid, Location.is_deleted.is_(False))).all()
    legacy = db.scalars(select(StockEntry).where(StockEntry.tenant_id == tid, StockEntry.kind == 'Return to supplier',
        StockEntry.id.not_in(select(SupplierReturn.stock_entry_id).where(SupplierReturn.tenant_id == tid,
            SupplierReturn.stock_entry_id.is_not(None)))).order_by(StockEntry.id.desc())).all()
    return {'lines': result, 'locations': [{'id': str(l.public_id), 'internalId': l.id, 'name': l.name} for l in locations],
        'legacy': [{'id': str(e.public_id), 'sku': e.variant_sku, 'quantity': abs(e.qty_delta),
                    'locationId': e.location_id, 'reference': e.reference} for e in legacy]}


def create(db, tid, payload, who):
    body = workflows.fingerprint(payload)
    prior = db.scalar(select(SupplierReturn).where(SupplierReturn.tenant_id == tid, SupplierReturn.request_id == payload.requestId))
    if prior:
        if prior.request_payload != body:
            raise HTTPException(409, 'Request ID already used for different return data')
        return {'id': str(prior.public_id)}
    line = row(db, PurchaseOrderLine, tid, payload.poLineId)
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.tenant_id == tid,
        PurchaseOrder.id == line.po_id).with_for_update().execution_options(populate_existing=True))
    if not po or not po.supplier_id or not line.variant_id or po.currency_code != 'EUR':
        raise HTTPException(409, 'An exact Catalog variant and EUR source PO with a saved supplier are required')
    variant = db.get(ProductVariant, line.variant_id)
    db.scalar(select(Product).where(Product.id == variant.product_id, Product.tenant_id == tid).with_for_update())
    location = row(db, Location, tid, payload.locationId)
    if location.is_deleted:
        raise HTTPException(409, 'Choose an active warehouse')
    if payload.quantity > source_received(db, tid, line):
        raise HTTPException(409, 'Return exceeds received quantity still eligible for return on this PO line')
    if not payload.reference.strip() or len(payload.reason.strip()) < 3:
        raise HTTPException(422, 'Enter a return reference and a meaningful reason')
    ret = SupplierReturn(tenant_id=tid, request_id=payload.requestId, request_payload=body,
        po_line_id=line.id, location_id=location.id, quantity=payload.quantity,
        amount=money(line.price * payload.quantity), resolution=payload.resolution,
        reference=payload.reference.strip(), reason=payload.reason.strip(), actor=who)
    db.add(ret); db.flush()
    if payload.legacyMovementId:
        if payload.resolution == 'Replacement':
            raise HTTPException(409, 'Historical stock-only returns can be linked for credit or refund. Their missing lot provenance cannot establish a traced replacement receipt')
        if payload.lotId:
            raise HTTPException(422, 'A historical movement already contains its batch stock disposition')
        entry = row(db, StockEntry, tid, payload.legacyMovementId, lock=True)
        if entry.kind != 'Return to supplier' or entry.variant_id != line.variant_id or entry.location_id != location.id or -entry.qty_delta != payload.quantity:
            raise HTTPException(409, 'Historical return must match the exact SKU, location and quantity')
        if db.scalar(select(SupplierReturn.id).where(SupplierReturn.stock_entry_id == entry.id)):
            raise HTTPException(409, 'This stock movement is already linked to a return')
        ret.stock_entry_id = entry.id
    else:
        lot = row(db, BatchLot, tid, payload.lotId, lock=True) if payload.lotId else None
        if lot:
            batch_line = db.get(BatchLine, lot.line_id)
            if batch_line.po_line_id != line.id or lot.location_id != location.id or lot.on_hand - lot.allocated < payload.quantity:
                raise HTTPException(409, 'Choose a matching source batch lot with enough unallocated stock')
        level = db.scalar(select(StockLevel).where(StockLevel.tenant_id == tid,
            StockLevel.variant_id == line.variant_id, StockLevel.location_id == location.id)
            .with_for_update().execution_options(populate_existing=True))
        if level is None or level.on_hand < payload.quantity:
            raise HTTPException(409, 'Insufficient warehouse stock')
        if lot:
            if level.reserved < payload.quantity or level.reserved < lot.on_hand or level.on_hand < level.reserved:
                raise HTTPException(409, 'Reconcile the warehouse and batch reservation balances first')
            lot.on_hand -= payload.quantity
            level.reserved -= payload.quantity
            ret.lot_id = lot.id
        elif level.on_hand - level.reserved < payload.quantity:
            raise HTTPException(409, 'Release reservations or select the source batch lot before returning stock')
        before = level.on_hand
        level.on_hand -= payload.quantity
        entry = ledger.record(db, level=level, before=before, kind='Return to supplier', request_id=payload.requestId,
            reference=ret.reference, reason=ret.reason, actor=who, source_module='procurement',
            source_type='supplier_return', source_id=ret.public_id, source_ref=f'SR-{ret.id}',
            batch_id=db.get(ProductionBatch, db.get(BatchLine, lot.line_id).batch_id).public_id if lot else None)
        ret.stock_entry_id = entry.id
        workflows.cache(db, tid, line.variant_id)
    if ret.resolution == 'Replacement':
        from app.modules.quality.repository import create_inspection
        ins = create_inspection(db, tenant_id=tid, order_ref=f'SR-{ret.id}', stage='Final', aql='2.5',
            sku=line.sku, product=line.name, prod_qty=ret.quantity, inspection_type='Supplier replacement')
        ret.inspection_id = ins.id
        ret.status = 'Awaiting replacement'
    audit(db, ret, f'{who}: {ret.quantity} {line.sku} returned against {po.po_no}; resolution {ret.resolution}; {ret.reference}; {ret.reason}')
    db.flush()
    return {'id': str(ret.public_id)}


def listing(db, tid, limit=50, offset=0):
    rows = db.execute(select(SupplierReturn, PurchaseOrderLine, PurchaseOrder).join(PurchaseOrderLine,
        PurchaseOrderLine.id == SupplierReturn.po_line_id).join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(SupplierReturn.tenant_id == tid).order_by(SupplierReturn.id.desc()).limit(limit).offset(offset)).all()
    result = []
    for ret, line, po in rows:
        ins = db.get(Inspection, ret.inspection_id) if ret.inspection_id else None
        result.append({'id': str(ret.public_id), 'ref': f'SR-{ret.id}', 'po': po.po_no,
            'poId': str(po.public_id), 'supplier': po.supplier_name, 'sku': line.sku,
            'quantity': ret.quantity, 'amount': str(ret.amount), 'resolution': ret.resolution,
            'status': ret.status, 'reference': ret.reference, 'reason': ret.reason,
            'actor': ret.actor, 'createdAt': ret.created_at.isoformat(),
            'events': workflows.history(db, tid, ret.public_id),
            'creditReference': ret.credit_reference, 'refundReference': ret.refund_reference,
            'inspectionId': str(ins.public_id) if ins else None, 'inspectionResult': ins.result if ins else None,
            'bills': [{'id': str(b.public_id), 'ref': b.bill_no} for b in db.scalars(select(SupplierBill).where(
                SupplierBill.tenant_id == tid, SupplierBill.po_ref == po.po_no, SupplierBill.is_deleted.is_(False))).all()]})
    total = db.scalar(select(func.count()).select_from(SupplierReturn).where(SupplierReturn.tenant_id == tid))
    return {'rows': result, 'total': total}


def action(db, tid, pid, payload, who, kind):
    ret = row(db, SupplierReturn, tid, pid, lock=True)
    if workflows.replay(db, tid, ret.public_id, kind, payload):
        return {'id': str(ret.public_id)}
    if not payload.reference.strip() or len(payload.reason.strip()) < 3:
        raise HTTPException(422, 'Enter a document reference and reason')
    line = db.get(PurchaseOrderLine, ret.po_line_id)
    po = db.get(PurchaseOrder, line.po_id)
    if kind == 'Change supplier resolution':
        if ret.refund_journal_id or ret.replaced or (ret.credit_journal_id and payload.resolution != 'Credit'):
            raise HTTPException(409, 'A settled return cannot change resolution')
        ret.resolution = payload.resolution
        ret.status = 'Closed' if ret.credit_journal_id else 'Awaiting supplier'
    elif kind == 'Supplier credit':
        if ret.resolution == 'Replacement' or ret.credit_journal_id or ret.amount <= 0:
            raise HTTPException(409, 'This return is not awaiting a supplier credit')
        bill = row(db, SupplierBill, tid, payload.billId, lock=True)
        if bill.is_deleted or bill.po_ref != po.po_no or bill.supplier_id != po.supplier_id or not bill.posted or not bill.gl_journal_id:
            raise HTTPException(409, 'Choose the posted payable for the same PO and supplier; synchronize Finance first if necessary')
        credited = db.scalar(select(func.coalesce(func.sum(SupplierReturn.amount), 0)).where(
            SupplierReturn.tenant_id == tid, SupplierReturn.supplier_bill_id == bill.id,
            SupplierReturn.credit_journal_id.is_not(None)))
        if money(credited + ret.amount) > money(bill.amount):
            raise HTTPException(409, 'Supplier credits cannot exceed the original payable')
        original = db.get(JournalEntry, bill.gl_journal_id)
        if original is None or original.tenant_id != tid:
            raise HTTPException(409, 'The original payable journal is missing')
        entries = db.scalars(select(JournalLine).where(JournalLine.tenant_id == tid, JournalLine.entry_id == original.id)).all()
        debits = [e for e in entries if e.debit > 0]
        credits = [e for e in entries if e.credit > 0]
        reversed_id = db.scalar(select(JournalEntry.id).where(JournalEntry.tenant_id == tid,
            JournalEntry.reversed_of_id == original.id, JournalEntry.is_deleted.is_(False)))
        if original.status != 'Posted' or original.is_deleted or reversed_id or len(debits) != 1 or len(credits) != 1:
            raise HTTPException(409, 'The original journal requires Finance reconciliation before an automatic return credit')
        from app.models.finance import Account
        accounts = [db.get(Account, e.account_id) if e.account_id else None for e in (debits[0], credits[0])]
        if any(a is None or a.tenant_id != tid or a.is_deleted or not a.is_active for a in accounts) or accounts[1].acct_type != 'Liability':
            raise HTTPException(409, 'Reconcile the original payable journal accounts before posting a return credit')
        if money(original.total_debit) != money(bill.amount) or money(original.total_credit) != money(bill.amount):
            raise HTTPException(409, 'The original payable and journal amounts differ; reconcile them in Finance first')
        from app.modules.finance import repository as finance
        if finance.is_closed_date(db, date.today()):
            raise HTTPException(409, 'Today falls in a closed accounting period')
        journal = finance.create_full_journal(db, tenant_id=tid, reference=None, entry_on=date.today(),
            entry_display=date.today().isoformat(), memo=f'Supplier return SR-{ret.id}: {payload.reference}'[:300],
            lines=[{'account': f'{accounts[1].code} · {accounts[1].name}', 'debit': ret.amount, 'credit': 0},
                   {'account': f'{accounts[0].code} · {accounts[0].name}', 'debit': 0, 'credit': ret.amount}])
        journal.source_note = f'supplier-return-credit:{ret.public_id}'
        ret.supplier_bill_id, ret.credit_journal_id = bill.id, journal.id
        ret.credit_reference, ret.credit_on = payload.reference.strip(), date.today()
        ret.status = 'Awaiting refund' if ret.resolution == 'Refund' else 'Closed'
        db.flush()
        applied = finance.disbursements_applied_to_bill(db, bill_no=bill.bill_no,
            supplier_id=bill.supplier_id, supplier_name=bill.supplier_name)
        bill.status = 'Paid' if money(str(applied)) >= money(bill.amount) else 'Open'
    elif kind == 'Supplier refund':
        if ret.resolution != 'Refund' or not ret.credit_journal_id or ret.refund_journal_id:
            raise HTTPException(409, 'Confirm the supplier credit before recording its refund')
        from app.modules.finance import repository as finance
        if finance.is_closed_date(db, date.today()):
            raise HTTPException(409, 'Today falls in a closed accounting period')
        asset = finance.find_account(db, acct_type='Asset', name_like=f'%{payload.channel}%')
        payable = db.scalar(select(JournalLine).where(JournalLine.entry_id == ret.credit_journal_id,
            JournalLine.tenant_id == tid, JournalLine.debit > 0))
        from app.models.finance import Account
        payable_account = db.get(Account, payable.account_id) if payable and payable.account_id else None
        if (not asset or not asset.is_active or not payable_account or payable_account.tenant_id != tid
                or payable_account.is_deleted or not payable_account.is_active):
            raise HTTPException(409, 'Configure the cash/bank account and reconcile the supplier credit first')
        # A cash refund may only clear a genuine supplier debit balance.
        from app.models.procurement import Supplier
        supplier = db.scalar(select(Supplier).where(Supplier.id == po.supplier_id,
            Supplier.tenant_id == tid).with_for_update().execution_options(populate_existing=True))
        if supplier is None:
            raise HTTPException(409, 'The source supplier is unavailable')
        # Serialize refunds for the same supplier before checking the advance.
        balance = sum(Decimal(str(e['debit'])) - Decimal(str(e['credit'])) for e in
            finance._supplier_entries(db, po.supplier_id, po.supplier_name))
        balance += Decimal(supplier.opening_balance or 0)
        if money(balance) < ret.amount:
            raise HTTPException(409, 'The supplier has no sufficient refundable advance. Apply the credit to open payables or reconcile its paid bill first')
        journal = finance.create_full_journal(db, tenant_id=tid, reference=None, entry_on=date.today(),
            entry_display=date.today().isoformat(), memo=f'Supplier refund SR-{ret.id}: {payload.reference}'[:300],
            lines=[{'account': f'{asset.code} · {asset.name}', 'debit': ret.amount, 'credit': 0},
                   {'account': f'{payable_account.code} · {payable_account.name}', 'debit': 0, 'credit': ret.amount}])
        journal.source_note = f'supplier-return-refund:{ret.public_id}'
        ret.refund_journal_id, ret.refund_on = journal.id, date.today()
        ret.refund_reference, ret.refund_channel, ret.status = payload.reference.strip(), payload.channel, 'Closed'
    elif kind == 'Receive supplier replacement':
        if ret.resolution != 'Replacement' or ret.replaced:
            raise HTTPException(409, 'This return is not awaiting replacement stock')
        ins = db.scalar(select(Inspection).where(Inspection.id == ret.inspection_id,
            Inspection.tenant_id == tid).with_for_update().execution_options(populate_existing=True))
        if not ins or ins.is_deleted or ins.result != 'Pass' or ins.prod_qty != ret.quantity or ins.sku != line.sku:
            raise HTTPException(409, 'Pass the linked replacement inspection in Quality before receiving stock')
        from app.modules.inventory.repository import add_stock
        db.scalar(select(Product).join(ProductVariant, ProductVariant.product_id == Product.id).where(
            Product.tenant_id == tid, ProductVariant.id == line.variant_id).with_for_update())
        lot = db.scalar(select(BatchLot).where(BatchLot.id == ret.lot_id,
            BatchLot.tenant_id == tid).with_for_update()) if ret.lot_id else None
        level = add_stock(db, tenant_id=tid, variant_id=line.variant_id, location_id=ret.location_id, qty=ret.quantity)
        if lot:
            lot.on_hand += ret.quantity
            level.reserved += ret.quantity
        ledger.record(db, level=level, before=level.on_hand-ret.quantity, kind='Receipt', request_id=payload.requestId,
            reference=payload.reference, reason=payload.reason, actor=who, source_module='procurement',
            source_type='supplier_return', source_id=ret.public_id, source_ref=f'SR-{ret.id}',
            batch_id=db.get(ProductionBatch, db.get(BatchLine, lot.line_id).batch_id).public_id if lot else None)
        workflows.cache(db, tid, line.variant_id)
        ret.replaced, ret.status = True, 'Closed'
    else:
        raise HTTPException(422, 'Unknown supplier return action')
    workflows.event(db, tid, ret.public_id, kind, payload, who)
    audit(db, ret, f'{who}: {kind}; {payload.reference}; {payload.reason}')
    db.flush()
    return {'id': str(ret.public_id)}
