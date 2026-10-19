"""A single buying form creates an atomic, supplier-specific set of purchase orders."""
import json
from types import SimpleNamespace
from sqlalchemy import select
from fastapi import HTTPException
from app.models.catalog import Product
from app.models.procurement import Supplier
from app.modules.inventory import workflows
from app.modules.procurement.dto import PurchaseOrderCreate, POLineIn


def create(db, tid, payload, who):
    previous = workflows.replay(db, tid, None, 'Create supplier POs', payload)
    if previous:
        return json.loads(previous.payload)['snapshot']
    from app.modules.sales.fulfillment import variant_for
    variants = [variant_for(db, SimpleNamespace(tenant_id=tid), line) for line in payload.lines]
    if any(v is None for v in variants):
        raise HTTPException(409, 'Choose exact saved article colour and size variants')
    # All article locks are acquired in one order before creating any supplier PO.
    db.scalars(select(Product).where(Product.tenant_id == tid,
        Product.id.in_({v.product_id for v in variants})).order_by(Product.id).with_for_update()).all()
    supplier_ids = {line.supplierId for line in payload.lines}
    suppliers = db.scalars(select(Supplier).where(Supplier.tenant_id == tid,
        Supplier.public_id.in_(supplier_ids), Supplier.is_deleted.is_(False)).order_by(Supplier.id)).all()
    if len(suppliers) != len(supplier_ids):
        raise HTTPException(409, 'Choose a valid saved supplier for every article')
    from app.modules.procurement import service
    result = []
    for supplier in suppliers:
        selected = [POLineIn(**line.model_dump(exclude={'supplierId'})) for line in payload.lines if line.supplierId == supplier.public_id]
        po = service.create_po(db, tenant_id=tid, resolved_supplier=supplier, payload=PurchaseOrderCreate(
            supplier=supplier.name, supplierId=supplier.public_id, expected=payload.expected, lines=selected))
        po.supplier_shipment_required = True
        result.append({'public_id': str(po.public_id), 'po_no': po.po_no,
            'supplier': supplier.name, 'supplierId': str(supplier.public_id), 'total': str(po.total)})
    response = {'requestId': str(payload.requestId), 'purchaseOrders': result}
    workflows.event(db, tid, payload.requestId, 'Create supplier POs', payload, who, snapshot=response)
    return response
