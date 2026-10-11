from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import tenant_db
from app.core.security import Principal, require_module, require_tenant
from app.core.roles import has_permission
from app.modules.batches import service, dto

router = APIRouter(prefix="/batches", tags=["batch fulfillment"])


@router.get("/{batch_id}/workspace")
def batch_workspace(batch_id: str, p: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    if not any(has_permission(p.permissions, f"{m}.read") or has_permission(p.permissions, f"{m}.write")
               for m in ("production", "quality", "inventory", "shipments")):
        raise HTTPException(403, "No access to batch work")
    batch = service.one(db, service.ProductionBatch, p.tenant_id, batch_id)
    work = db.get(service.ProductionOrder, batch.order_id)
    return {"orderId": str(work.public_id)}


@router.get("/work/{kind}")
def work(kind: str, p: Principal = Depends(require_module("production", "read")), db: Session = Depends(tenant_db)):
    return service.work_list(db, p.tenant_id, kind)


@router.get("/quality-work")
def quality_work(p: Principal = Depends(require_module("quality", "read")), db: Session = Depends(tenant_db)):
    return service.work_list(db, p.tenant_id, "quality")


@router.get("/orders/{order_id}")
def order(order_id: str, kind: str = Query("production", pattern="^(production|sales|procurement)$"),
          p: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    modules = [kind] if kind != "production" else ["production", "quality", "inventory", "shipments"]
    if not any(has_permission(p.permissions, f"{m}.read") or has_permission(p.permissions, f"{m}.write") for m in modules):
        raise HTTPException(403, "No access to this order's batch workspace")
    return service.view(db, p.tenant_id, order_id, kind)


@router.get("/shipments/{shipment_id}")
def shipment_view(shipment_id: str, p: Principal = Depends(require_module("shipments", "read")), db: Session = Depends(tenant_db)):
    ship = service.one(db, service.Shipment, p.tenant_id, shipment_id)
    allocations = service.rows(db, service.BatchAllocation, p.tenant_id, service.BatchAllocation.shipment_id == ship.id)
    if not ship.batch_managed: return None
    if ship.sales_order_id:
        data = service.view(db, p.tenant_id, db.get(service.SalesOrder, ship.sales_order_id).public_id, "sales")
    else:
        line = db.get(service.BatchLine, db.get(service.BatchLot, allocations[0].lot_id).line_id)
        work = db.get(service.ProductionOrder, db.get(service.ProductionBatch, line.batch_id).order_id)
        data = service.view(db, p.tenant_id, work.public_id, "production")
    return next(s for s in data["shipments"] if s["publicId"] == shipment_id)


@router.post("/orders/{order_id}", status_code=201)
def create(order_id: str, command: dto.CreateBatch,
           p: Principal = Depends(require_module("production", "write")), db: Session = Depends(tenant_db)):
    return service.create_batch(db, p.tenant_id, p.user_id or p.email, order_id, command)


@router.post("/{batch_id}/stages/{stage_id}")
def stage(batch_id: str, stage_id: int, command: dto.StageCommand,
          p: Principal = Depends(require_module("production", "write")), db: Session = Depends(tenant_db)):
    return service.stage(db, p.tenant_id, p.user_id or p.email, batch_id, stage_id, command)


@router.post("/{batch_id}/cancel")
def cancel(batch_id: str, command: dto.Command,
           p: Principal = Depends(require_module("production", "write")), db: Session = Depends(tenant_db)):
    return service.cancel_batch(db, p.tenant_id, p.user_id or p.email, batch_id, command)


@router.post("/{batch_id}/lines/{line_id}/complete")
def complete(batch_id: str, line_id: int, command: dto.Quantity,
             p: Principal = Depends(require_module("production", "write")), db: Session = Depends(tenant_db)):
    return service.line_action(db, p.tenant_id, p.user_id or p.email, batch_id, line_id, command, "Complete production")


@router.post("/{batch_id}/lines/{line_id}/qc")
def qc(batch_id: str, line_id: int, command: dto.QC,
       p: Principal = Depends(require_module("quality", "write")), db: Session = Depends(tenant_db)):
    return service.line_action(db, p.tenant_id, p.user_id or p.email, batch_id, line_id, command, "QC")


@router.post("/{batch_id}/lines/{line_id}/receive")
def receive(batch_id: str, line_id: int, command: dto.Quantity,
            p: Principal = Depends(require_module("inventory", "write")), db: Session = Depends(tenant_db)):
    return service.line_action(db, p.tenant_id, p.user_id or p.email, batch_id, line_id, command, "Receive approved stock")


@router.post("/{batch_id}/lots/{lot_id}/transfer")
def transfer(batch_id: str, lot_id: int, command: dto.Quantity,
             p: Principal = Depends(require_module("inventory", "write")), db: Session = Depends(tenant_db)):
    return service.transfer_lot(db, p.tenant_id, p.user_id or p.email, batch_id, lot_id, command)


@router.post("/orders/{order_id}/shipments", status_code=201)
def shipment(order_id: str, command: dto.CreateShipment,
             kind: str = Query("production", pattern="^(production|sales|procurement)$"),
             p: Principal = Depends(require_module("shipments", "write")), db: Session = Depends(tenant_db)):
    return service.create_shipment(db, p.tenant_id, p.user_id or p.email, order_id, kind, command)


@router.post("/shipments/{shipment_id}/status")
def shipment_status(shipment_id: str, command: dto.ShipmentStatus,
                    p: Principal = Depends(require_module("shipments", "write")), db: Session = Depends(tenant_db)):
    if command.status == "Returned" and not has_permission(p.permissions, "inventory.write"):
        raise HTTPException(403, "Warehouse write access is required to confirm returned pieces")
    ship = service.one(db, service.Shipment, p.tenant_id, shipment_id)
    if ship.sales_order_id:
        service.parent(db, p.tenant_id, db.get(service.SalesOrder, ship.sales_order_id).public_id, "sales", lock=True)
    # Parent locks serialize commands, including retries after dispatch.
    allocations = service.rows(db, service.BatchAllocation, p.tenant_id, service.BatchAllocation.shipment_id == ship.id)
    for a in allocations:
        line = db.get(service.BatchLine, db.get(service.BatchLot, a.lot_id).line_id)
        batch = db.get(service.ProductionBatch, line.batch_id)
        service.locked_batch(db, p.tenant_id, batch.public_id)
    prior, payload = service.begin(db, p.tenant_id, command, "Shipment status", shipment_id)
    if prior is not None: return prior
    ship = service.shipment_status(db, p.tenant_id, shipment_id, command.status)
    return service.end(db, p.tenant_id, p.user_id or p.email, command, "Shipment status", payload, {"status": ship.status})
