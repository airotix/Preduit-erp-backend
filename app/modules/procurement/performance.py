"""Evidence-based supplier performance and internal follow-up records."""
from collections import defaultdict
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.core.audit import write_audit
from app.core.roles import permissions_for, has_permission
from app.models.core import User
from app.models.procurement import Supplier, PurchaseOrder, PurchaseOrderLine, GoodsReceipt, GoodsReceiptLine
from app.models.production import ProductionOrder
from app.models.quality import Inspection
from app.models.supplier_returns import SupplierReturn
from app.models.supplier_followups import SupplierFollowup
from app.modules.inventory import workflows


def iso_date(value):
    """Do not assign a year to a legacy '12 Sep' label or invent a receipt date."""
    try:
        if not isinstance(value, str) or len(value) != 10:
            return None
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except ValueError:
        return None


def pct(n, d):
    return round(100 * n / d, 1) if d else None


def receipt_date(value):
    result = iso_date(value)
    if result is not None:
        return result
    try:
        # Older receipts include a four-digit year, unlike ambiguous day/month labels.
        return datetime.strptime(value, '%d %b %Y').date() if value and len(value.split()[-1]) == 4 else None
    except (ValueError, TypeError, IndexError):
        return None


def state_token(db, followup):
    from app.models.inventory_operations import WorkflowEvent
    latest = db.scalar(select(WorkflowEvent.id).where(WorkflowEvent.tenant_id == followup.tenant_id,
        WorkflowEvent.scope_id == followup.public_id).order_by(WorkflowEvent.id.desc()).limit(1))
    return hashlib.sha256(json.dumps([followup.owner, followup.due_on.isoformat(), followup.status, latest]).encode()).hexdigest()


def calculate(db, suppliers):
    if not suppliers:
        return {}, []
    tids = {s.tenant_id for s in suppliers}
    pos = db.scalars(select(PurchaseOrder).where(PurchaseOrder.tenant_id.in_(tids),
        PurchaseOrder.supplier_id.in_([s.id for s in suppliers]), PurchaseOrder.is_deleted.is_(False))).all()
    po_by_id = {p.id: p for p in pos}
    by_ref = defaultdict(list)
    for po in pos:
        by_ref[(po.tenant_id, po.po_no)].append(po)
    lines = db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.tenant_id.in_(tids),
        PurchaseOrderLine.po_id.in_(po_by_id))).all()
    lines_by_po = defaultdict(list)
    for line in lines:
        lines_by_po[line.po_id].append(line)
    receipts = db.execute(select(GoodsReceiptLine, GoodsReceipt).join(GoodsReceipt,
        GoodsReceipt.id == GoodsReceiptLine.grn_id).where(GoodsReceipt.tenant_id.in_(tids),
        GoodsReceiptLine.tenant_id.in_(tids), GoodsReceipt.is_deleted.is_(False),
        GoodsReceipt.po_ref.in_([p.po_no for p in pos if p.po_no]))).all()
    received, dated_receipts = defaultdict(int), defaultdict(list)
    gross_by_supplier, unknown_by_supplier = defaultdict(int), defaultdict(int)
    undated, unmapped_pos = set(), set()
    for entry, receipt in receipts:
        candidates = by_ref[(receipt.tenant_id, receipt.po_ref)]
        if len(candidates) != 1 or entry.received <= 0:
            continue
        po = candidates[0]
        gross_by_supplier[po.supplier_id] += entry.received
        possible = [l for l in lines_by_po[po.id] if l.id == entry.po_line_id] if entry.po_line_id else [
            l for l in lines_by_po[po.id] if entry.sku and l.sku == entry.sku]
        if len(possible) != 1:
            unknown_by_supplier[po.supplier_id] += entry.received
            unmapped_pos.add(po.id)
            continue
        line = possible[0]
        received[line.id] += entry.received
        when = receipt.received_on or receipt_date(receipt.received_date)
        if when is None:
            undated.add(po.id)
        else:
            dated_receipts[line.id].append((when, entry.received))
    works = db.scalars(select(ProductionOrder).where(ProductionOrder.tenant_id.in_(tids),
        ProductionOrder.purchase_order_id.in_(po_by_id))).all()
    works_by_ref = defaultdict(list)
    for work in works:
        works_by_ref[(work.tenant_id, work.order_no)].append(work)
    inspections = db.scalars(select(Inspection).where(Inspection.tenant_id.in_(tids),
        Inspection.order_ref.in_([w.order_no for w in works if w.order_no]),
        Inspection.stage == 'Final', Inspection.result.in_(['Pass', 'Fail']), Inspection.is_deleted.is_(False))
        .order_by(Inspection.id)).all()
    quality = defaultdict(list)
    for ins in inspections:
        linked = works_by_ref[(ins.tenant_id, ins.order_ref)]
        if len(linked) == 1:
            work = linked[0]
            # Count each style/batch once; repeat inspections do not erase first failure.
            key = (work.purchase_order_id, ins.batch_line_id or (work.id, ins.item or ins.sku or 'order'))
            quality[key].append(ins)
    returns = db.scalars(select(SupplierReturn).join(PurchaseOrderLine,
        PurchaseOrderLine.id == SupplierReturn.po_line_id).where(SupplierReturn.tenant_id.in_(tids),
        PurchaseOrderLine.po_id.in_(po_by_id))).all()
    po_by_line = {l.id: po_by_id[l.po_id] for l in lines}
    metrics, attention = {}, []
    internal = {}
    for supplier in suppliers:
        m = {'supplierId': str(supplier.public_id), 'name': supplier.name, 'status': supplier.status,
            'completed': 0, 'measuredDeliveries': 0, 'onTime': 0, 'overdue': 0, 'missingDueDates': 0,
            'qualityLots': 0, 'passedFirstTime': 0, 'returnedUnits': 0, 'replacementUnits': 0,
            'receivedUnits': gross_by_supplier[supplier.id], 'unmappedReceiptUnits': unknown_by_supplier[supplier.id],
            'openReturns': 0}
        metrics[str(supplier.public_id)] = m
        internal[supplier.id] = m
    for po in pos:
        m = internal[po.supplier_id]
        source_lines = lines_by_po[po.id]
        complete = bool(source_lines) and all(received[l.id] >= l.qty for l in source_lines)
        due = po.original_due_on
        if po.status not in ('Cancelled', 'Rejected', 'Inactive', 'Pending approval'):
            if due is None:
                m['missingDueDates'] += 1
            if complete:
                m['completed'] += 1
                if due is not None and po.id not in undated:
                    m['measuredDeliveries'] += 1
                    if all(sum(qty for when, qty in dated_receipts[l.id] if when <= due) >= l.qty for l in source_lines):
                        m['onTime'] += 1
            current_due = receipt_date(po.expected)
            if po.id in unmapped_pos:
                attention.append({'supplierId': m['supplierId'], 'supplier': m['name'], 'sourceType': 'po',
                    'sourceId': str(po.public_id), 'reference': po.po_no, 'href': f'/procurement/pos/{po.public_id}',
                    'message': 'Reconcile ambiguous receipt lines before assessing delivery performance', 'due': None})
            elif not complete and not po.inventory_received and po.status != 'Received' and current_due and current_due < date.today():
                m['overdue'] += 1
                attention.append({'supplierId': m['supplierId'], 'supplier': m['name'], 'sourceType': 'po',
                    'sourceId': str(po.public_id), 'reference': po.po_no, 'href': f'/procurement/pos/{po.public_id}',
                    'message': f'Outstanding delivery, due {current_due.isoformat()}', 'due': current_due.isoformat()})
    for (po_id, _), exams in quality.items():
        m = internal[po_by_id[po_id].supplier_id]
        m['qualityLots'] += 1
        m['passedFirstTime'] += int(exams[0].result == 'Pass')
        if exams[-1].result == 'Fail':
            ins = exams[-1]
            attention.append({'supplierId': m['supplierId'], 'supplier': m['name'], 'sourceType': 'inspection',
                'sourceId': str(ins.public_id), 'reference': ins.inspection_no, 'href': f'/quality/inspections/{ins.public_id}',
                'message': 'Final QC failed; agree corrective action with the supplier', 'due': None})
    for ret in returns:
        po = po_by_line[ret.po_line_id]
        m = internal[po.supplier_id]
        m['returnedUnits'] += ret.quantity
        m['replacementUnits'] += ret.quantity if ret.replaced else 0
        if ret.status != 'Closed':
            m['openReturns'] += 1
            attention.append({'supplierId': m['supplierId'], 'supplier': m['name'], 'sourceType': 'return',
                'sourceId': str(ret.public_id), 'reference': f'SR-{ret.id}', 'href': '/procurement/returns',
                'message': f'{ret.status}: {ret.quantity} units / EUR {ret.amount}', 'due': None})
    for m in metrics.values():
        m['onTimePct'] = pct(m['onTime'], m['measuredDeliveries'])
        m['firstPassPct'] = pct(m['passedFirstTime'], m['qualityLots'])
        m['returnRate'] = pct(m['returnedUnits'], m['receivedUnits'] + m['replacementUnits'])
        m['unmeasuredCompleted'] = m['completed'] - m['measuredDeliveries']
    return metrics, attention


def metrics_for(db, public_ids):
    suppliers = db.scalars(select(Supplier).where(Supplier.public_id.in_([UUID(str(p)) for p in public_ids]),
        Supplier.is_deleted.is_(False))).all()
    return calculate(db, suppliers)[0]


def owners(db, tid):
    return [u for u in db.scalars(select(User).where(User.tenant_id == tid, User.is_active.is_(True), User.status == 'Active')).all()
        if u.email and has_permission(permissions_for(u.role, u.is_platform_admin), 'procurement.write')]


def dashboard(db, tid, supplier_id=None):
    q = select(Supplier).where(Supplier.tenant_id == tid, Supplier.is_deleted.is_(False))
    if supplier_id:
        q = q.where(Supplier.public_id == supplier_id)
    suppliers = db.scalars(q.order_by(Supplier.name)).all()
    metrics, attention = calculate(db, suppliers)
    followups = db.scalars(select(SupplierFollowup).where(SupplierFollowup.tenant_id == tid,
        SupplierFollowup.supplier_id.in_([s.id for s in suppliers])).order_by(
            SupplierFollowup.due_on, SupplierFollowup.id)).all()
    by_id = {s.id: s for s in suppliers}
    return {'suppliers': list(metrics.values()), 'attention': attention,
        'owners': [{'email': u.email, 'name': u.display_name or u.email} for u in owners(db, tid)],
        'followups': [{'id': str(f.public_id), 'supplierId': str(by_id[f.supplier_id].public_id),
            'supplier': by_id[f.supplier_id].name, 'sourceType': f.source_type, 'sourceId': str(f.source_id) if f.source_id else None,
            'summary': f.summary, 'owner': f.owner, 'due': f.due_on.isoformat(), 'status': f.status,
            'overdue': f.status != 'Resolved' and f.due_on < date.today(), 'resolution': f.resolution,
            'revision': state_token(db, f),
            'events': workflows.history(db, tid, f.public_id)} for f in followups]}


class FollowupIn(BaseModel):
    requestId: UUID
    supplierId: UUID
    sourceType: Literal['po', 'inspection', 'return'] | None = None
    sourceId: UUID | None = None
    summary: str = Field(min_length=3, max_length=240)
    owner: str = Field(min_length=3, max_length=256)
    due: date


class FollowupUpdate(BaseModel):
    requestId: UUID
    revision: str = Field(min_length=64, max_length=64)
    owner: str = Field(min_length=3, max_length=256)
    due: date
    status: Literal['Open', 'In progress', 'Resolved']
    reason: str = Field(min_length=3, max_length=1000)


def create_followup(db, tid, payload, who):
    body = workflows.fingerprint(payload)
    prior = db.scalar(select(SupplierFollowup).where(SupplierFollowup.tenant_id == tid,
        SupplierFollowup.request_id == payload.requestId))
    if prior:
        if prior.request_payload != body:
            raise HTTPException(409, 'Request ID already used for another follow-up')
        return {'id': str(prior.public_id)}
    supplier = db.scalar(select(Supplier).where(Supplier.tenant_id == tid,
        Supplier.public_id == payload.supplierId, Supplier.is_deleted.is_(False)).with_for_update())
    if supplier is None:
        raise HTTPException(404, 'Supplier not found')
    owner = next((u.email for u in owners(db, tid) if u.email.lower() == payload.owner.strip().lower()), None)
    if owner is None or len(payload.summary.strip()) < 3:
        raise HTTPException(422, 'Choose an active Procurement owner and enter a meaningful summary')
    if bool(payload.sourceType) != bool(payload.sourceId):
        raise HTTPException(422, 'Choose both source type and source ID')
    if payload.sourceId:
        _, attention = calculate(db, [supplier])
        if not any(a['sourceType'] == payload.sourceType and a['sourceId'] == str(payload.sourceId) for a in attention):
            raise HTTPException(409, 'This issue is no longer outstanding for the selected supplier. Refresh the queue')
        existing = db.scalar(select(SupplierFollowup.id).where(SupplierFollowup.tenant_id == tid,
            SupplierFollowup.source_type == payload.sourceType, SupplierFollowup.source_id == payload.sourceId,
            SupplierFollowup.status != 'Resolved'))
        if existing:
            raise HTTPException(409, 'An active follow-up already exists for this issue')
    f = SupplierFollowup(tenant_id=tid, supplier_id=supplier.id, source_type=payload.sourceType,
        source_id=payload.sourceId, summary=payload.summary.strip(), owner=owner, due_on=payload.due,
        created_by=who, request_id=payload.requestId, request_payload=body)
    db.add(f); db.flush()
    workflows.event(db, tid, f.public_id, 'Create supplier follow-up', payload, who)
    write_audit(db, tenant_id=tid, action='CREATE', entity_type='supplier_followup', entity_id=str(f.public_id),
        detail=f'{who}: {f.summary}; assigned to {f.owner}, due {f.due_on}')
    return {'id': str(f.public_id)}


def update_followup(db, tid, pid, payload, who):
    f = db.scalar(select(SupplierFollowup).where(SupplierFollowup.tenant_id == tid,
        SupplierFollowup.public_id == pid).with_for_update().execution_options(populate_existing=True))
    if f is None:
        raise HTTPException(404, 'Follow-up not found')
    if workflows.replay(db, tid, f.public_id, 'Update supplier follow-up', payload):
        return {'id': str(f.public_id)}
    if payload.revision != state_token(db, f):
        raise HTTPException(409, 'This follow-up changed since it was opened. Refresh and reopen it before saving')
    if f.status == 'Resolved':
        raise HTTPException(409, 'A resolved follow-up is retained as history. Create a new follow-up if needed')
    owner = next((u.email for u in owners(db, tid) if u.email.lower() == payload.owner.strip().lower()), None)
    if not owner or len(payload.reason.strip()) < 3:
        raise HTTPException(422, 'Choose an active Procurement owner and enter a reason or resolution note')
    snapshot = {'owner': f.owner, 'due': f.due_on.isoformat(), 'status': f.status}
    f.owner, f.due_on, f.status = owner, payload.due, payload.status
    if f.status == 'Resolved':
        f.resolution = payload.reason.strip()
        f.resolved_at = datetime.now(timezone.utc)
    workflows.event(db, tid, f.public_id, 'Update supplier follow-up', payload, who, snapshot=snapshot)
    write_audit(db, tenant_id=tid, action='UPDATE', entity_type='supplier_followup', entity_id=str(f.public_id),
        detail=f'{who}: {f.status}; {payload.reason.strip()}')
    db.flush()
    return {'id': str(f.public_id)}
