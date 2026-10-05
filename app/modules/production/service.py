"""Production business logic → frontend ScreenConfig."""
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.production import repository as repo
from app.modules import techpacks
from app.models.production import ProductionOrder, ProductionOrderLine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.modules.production.dto import BomCreate, BomUpdate
from app.presenters.screen import board_config, initials, list_config, text_cell

STAGES = ["Tech Pack", "Trims", "Lining", "Cutting", "Sewing", "Finishing", "Packed"]
_STAGE_TONE = {"Trims": "neutral", "Lining": "neutral", "Cutting": "navy",
               "Sewing": "amber", "Finishing": "green", "Packed": "green", "Completed": "green"}
_BOARD = [
    ("Tech Pack", "Tech Pack", "#2563EB", "navy", "file-text"),
    ("Trims", "Trims", "#9499A6", "neutral", "activity"),
    ("Lining", "Lining", "#9499A6", "neutral", "activity"),
    ("Cutting", "Cutting", "#3A4256", "navy", "activity"),
    ("Sewing", "Sewing", "#D29A22", "amber", "activity"),
    ("Finishing", "Finishing", "#2E9E6B", "green", "activity"),
    ("Packed", "Packed", "#2E9E6B", "green", "check"),
]


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


def porders_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    from app.modules.techpacks import sync_orders
    sync_orders(session)
    rows, total = repo.list_porders(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["order_no"] or "—", strong=True, mono=True),
            r["style"],
            r.get("source_po") or "—",
            r.get("supplier") or r.get("customer") or "—",
            text_cell(f"{r['qty']:,}", align="right", mono=True),
            text_cell(r["stage"], badge=_STAGE_TONE.get(r["stage"], "neutral")),
            text_cell(f"{r['progress']}%", align="right", mono=True, strong=True),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Order"}, {"label": "Style"}, {"label": "Source PO"}, {"label": "Supplier / Customer"},
                 {"label": "Qty", "align": "right"}, {"label": "Stage"},
                 {"label": "Progress", "align": "right"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["stage"], "started": r.get("started", False),
                  "shippable": r.get("shippable", False), "shipped": r.get("shipped", False)}
                 for r in rows],
        search="Search production orders…", filters=["Stage"],
    )


def ship_order(session, *, tenant_id, public_id, carrier, eta, destination):
    """Log a completed production order as a shipment (Shipments module)."""
    from app.models.shipments import ShipmentLine
    from app.modules.shipments import repository as ship_repo
    po = repo.order_by_public(session, public_id)
    if po is None:
        return None
    if po.purchase_order_id:
        source = session.get(PurchaseOrder, po.purchase_order_id)
        if source:
            from fastapi import HTTPException
            raise HTTPException(409, "Procurement production enters inventory after final QC; fully stocked sales orders ship together.")
    s = ship_repo.create_shipment(session, tenant_id=_tid(tenant_id), order_ref=po.order_no,
                                  carrier=carrier, destination=destination)
    s.eta = eta
    session.add(ShipmentLine(tenant_id=s.tenant_id, shipment_id=s.id, sku=None,
                             description=po.style, qty=po.qty))
    session.flush()
    return s


def _line_is_complete(session, line_id: int) -> bool:
    stages = repo.list_line_stages(session, line_id)
    return bool(stages) and all(s.status in ("Completed", "Received") for s in stages)


def _open_line_inspection(session, *, tenant_id, order_ref, line: ProductionOrderLine) -> None:
    """Create a Final inspection for one production item (line name), idempotent
    per order + item — so each item is inspected independently of the others.
    Quantity = the sum across all lines sharing that item name."""
    from app.modules.quality import repository as qc_repo
    item = line.name
    if qc_repo.inspection_exists_for_item(session, order_ref=order_ref, item=item):
        return
    qty = sum(int(l.qty or 0) for l in repo.list_lines(session, line.order_id) if l.name == item)
    qc_repo.create_inspection(session, tenant_id=_tid(tenant_id),
                              order_ref=order_ref, item=item, stage="Final", aql="2.5",
                              product=line.name, prod_qty=qty or line.qty, sku=line.sku)


def send_for_inspection(session, *, tenant_id, public_id):
    """Open a QC inspection for every production line (item) that has finished
    production. Each completed item gets its own inspection — the order does NOT
    have to wait for all items to finish."""
    po = repo.order_by_public(session, public_id)
    if po is None:
        return None
    lines = repo.list_lines(session, po.id)
    made_any = False
    for ln in lines:
        if _line_is_complete(session, ln.id):
            _open_line_inspection(session, tenant_id=tenant_id, order_ref=po.order_no, line=ln)
            made_any = True
    # Legacy fallback: an order with no per-line stages → one order-level inspection.
    if not lines:
        from app.modules.quality import repository as qc_repo
        if not qc_repo.inspection_exists(session, order_ref=po.order_no):
            qc_repo.create_inspection(session, tenant_id=_tid(tenant_id),
                                      order_ref=po.order_no, stage="Final", aql="2.5",
                                      product=po.style, prod_qty=po.qty)
    return po


def create_for_purchase_order(session, *, tenant_id, purchase_order: PurchaseOrder):
    """One unstarted work order per procurement PO, one TNA item per style.

    Lock the source PO so repeated/concurrent calls cannot create duplicates.
    Each variant line retains its details and links to its production item.
    No commit here: procurement and production share the request transaction.
    """
    tid = _tid(tenant_id)
    source = session.execute(select(PurchaseOrder).where(
        PurchaseOrder.id == purchase_order.id, PurchaseOrder.tenant_id == tid,
        PurchaseOrder.is_deleted == False).with_for_update()).scalar_one_or_none()
    if source is None:
        raise ValueError("Purchase order does not belong to this tenant")
    existing = session.execute(select(ProductionOrder).where(
        ProductionOrder.purchase_order_id == source.id,
        ProductionOrder.tenant_id == tid)).scalar_one_or_none()
    if existing is not None:
        return existing
    lines = session.execute(select(PurchaseOrderLine).where(
        PurchaseOrderLine.po_id == source.id, PurchaseOrderLine.tenant_id == tid)
        .order_by(PurchaseOrderLine.id)).scalars().all()
    if not lines:
        raise ValueError("Purchase order must have line items")
    styles: dict[str, list[PurchaseOrderLine]] = {}
    for line in lines:
        styles.setdefault(line.name.strip(), []).append(line)
    names = list(styles)
    label = names[0] if len(names) == 1 else f"{names[0][:180]} +{len(names) - 1} more"
    work = repo.create_porder(session, tenant_id=tid, style=label, factory=None,
        qty=sum(line.qty for line in lines), purchase_order_id=source.id)
    for name, variants in styles.items():
        single = variants[0] if len(variants) == 1 else None
        item = repo.create_porder_line(session, tenant_id=tid, order_id=work.id,
            name=name, qty=sum(line.qty for line in variants),
            sku=single.sku if single else None, color=single.color if single else None,
            size=single.size if single else None)
        for variant in variants:
            variant.production_line_id = item.id
    session.flush()
    return work


def set_stage(session, *, public_id, status):
    return repo.set_stage(session, public_id=public_id, stage=status)


def board_screen(session: Session) -> dict:
    from app.modules import techpacks
    techpacks.sync_orders(session)
    rows = repo.list_porders_all(session)
    columns = []
    for stage, title, accent, tone, meta_icon in _BOARD:
        cards = []
        for r in rows:
            if r["stage"] != stage:
                continue
            cards.append({
                "public_id": str(r["public_id"]),
                "href": f"/production/porders/{r['public_id']}",
                "ref": r["order_no"] or "—",
                "title": r["style"],
                "sub": f"{r['qty']:,} units · {r['factory'] or '—'}",
                "meta": f"{r['progress']}%",
                "metaIcon": meta_icon,
                "av": initials(r["style"]),
                "tone": tone,
                "techPacks": [{"lineId": str(line.public_id), "name": line.name,
                               **techpacks.line_pack(session, line)}
                              for line in repo.list_lines(session, r["id"])] if stage == "Tech Pack" else [],
            })
        columns.append({"title": title, "accent": accent, "count": len(cards), "cards": cards})
    return board_config(columns)


def bom_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_bom(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["component"], strong=True),
            r["style"] or "—",
            r["material"] or "—",
            text_cell(r["qty_per_unit"] or "—", align="right", mono=True),
            text_cell(f"€{r['cost']:,.2f}", align="right", mono=True, strong=True),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Component"}, {"label": "Style"}, {"label": "Material"},
                 {"label": "Qty / unit", "align": "right"}, {"label": "Cost", "align": "right"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"component": r["component"], "style": r["style"],
                  "material": r["material"], "qtyPerUnit": r["qty_per_unit"],
                  "cost": float(r["cost"]) if r["cost"] is not None else None}
                 for r in rows],
        search="Search BOMs…", action="New BOM", filters=["Style"],
    )


def _bom_fields(p) -> dict:
    return {"component": p.component, "style": p.style, "material": p.material,
            "qty_per_unit": p.qtyPerUnit, "cost": p.cost}


def create_bom(session, *, tenant_id, payload: BomCreate):
    return repo.create_bom(session, tenant_id=_tid(tenant_id), **_bom_fields(payload))


def update_bom(session, *, public_id, payload: BomUpdate):
    return repo.update_bom(session, public_id=public_id, **_bom_fields(payload))


# ---------- Production order detail (stage timeline) ----------

def _fmt(d) -> str:
    return d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else "—"


_LINE_TONE = {"Completed": "green", "In Progress": "accent", "Planned": "neutral"}


def _stage_rows(stages, today) -> list[dict]:
    return [
        {
            "public_id": str(s.public_id), "seq": s.seq, "name": s.name,
            "duration_days": s.duration_days, "status": s.status,
            "overdue": s.status not in ("Completed", "Received") and s.end_on is not None and s.end_on < today,
            "start": _fmt(s.start_on), "end": _fmt(s.end_on),
            "worker": s.worker or "Unassigned", "notes": s.notes or "",
        }
        for s in stages
    ]


def _timeline_alert(stages, today, *, started: bool, all_done: bool):
    if all_done:
        return {"type": "done", "message": "All stages completed — production is finished."}
    if not started:
        return None
    active = next((s for s in stages if s.status not in ("Completed", "Received")), None)
    if active is None:
        return None
    if active.name == "Tech Pack":
        return {"type": "waiting", "message": "Upload the article’s tech pack to mark this stage Received."}
    if active.status == "In Progress":
        if active.end_on and active.end_on < today:
            return {"type": "overdue",
                    "message": f"“{active.name}” is overdue — its {active.duration_days} "
                               f"days elapsed on {_fmt(active.end_on)}. Mark it complete or extend."}
        if active.end_on == today:
            return {"type": "due",
                    "message": f"“{active.name}” is due today — complete it to move to the next stage."}
        return {"type": "progress", "message": f"“{active.name}” is in progress — due {_fmt(active.end_on)}."}
    return {"type": "waiting", "message": f"“{active.name}” is waiting to start."}


def _timeline(stages, today) -> dict:
    started = any(s.name != "Tech Pack" for s in stages)
    completed = sum(1 for s in stages if s.status in ("Completed", "Received"))
    progress = round(completed / len(stages) * 100) if stages else 0
    all_done = started and completed == len(stages)
    status_label = "Completed" if all_done else ("In Progress" if started else "Planned")
    return {
        "started": started, "progress": progress, "statusLabel": status_label,
        "statusTone": _LINE_TONE.get(status_label, "neutral"),
        "stages": _stage_rows(stages, today),
        "alert": _timeline_alert(stages, today, started=started, all_done=all_done),
    }


def porder_detail(session: Session, *, public_id: str) -> dict | None:
    import datetime
    from app.modules import techpacks
    order = repo.order_by_public(session, public_id)
    if order is not None:
        techpacks.sync_order(session, order)
    data = repo.get_porder_detail(session, public_id=public_id)
    if data is None:
        return None
    po, mats, stages = data["order"], data["materials"], data["stages"]
    sales_order, order_lines = data.get("sales_order"), data.get("order_lines") or []
    purchase_order = data.get("purchase_order")
    today = datetime.date.today()

    def _money(v) -> str:
        if purchase_order is not None:
            return f"{purchase_order.currency_code} {float(v or 0):,.2f}"
        return f"€{float(v or 0):,.2f}"

    order_lines_out = [
        {"item": l["name"], "color": (l["color"] or ""), "size": (l["size"] or "—"),
         "qty": int(l["qty"] or 0), "price": _money(l["price"]), "total": _money(l["line_total"])}
        for l in order_lines
    ]
    order_total = _money(sum(float(l["line_total"] or 0) for l in order_lines))

    # Overall (all lines' stages) drives the header + Start button.
    overall = _timeline(stages, today)
    started, progress = overall["started"], overall["progress"]
    status_label, alert = overall["statusLabel"], overall["alert"]

    # Workflow chain: once every stage is complete, offer "Send for inspection"
    # (unless an inspection was already opened for this order).
    from app.modules.quality import repository as qc_repo
    inspected = qc_repo.inspection_exists(session, order_ref=po.order_no)
    can_inspect = started and progress == 100 and not inspected

    # One timeline per production line (style) → tabs on the detail page.
    lines_out = [
        {"publicId": str(ld["line"].public_id), "name": ld["line"].name,
         "qty": int(ld["line"].qty or 0), "techPack": techpacks.line_pack(session, ld["line"]),
         **_timeline(ld["stages"], today)}
        for ld in (data.get("lines") or [])
    ]

    meta = []
    if purchase_order is not None:
        meta.append({"k": "Source PO", "v": purchase_order.po_no or "—"})
        meta.append({"k": "Supplier", "v": purchase_order.supplier_name})
    elif sales_order is not None:
        meta.append({"k": "Order", "v": sales_order.order_no or "—"})
        meta.append({"k": "Customer", "v": sales_order.customer_name or "—"})
    else:
        meta.append({"k": "Factory", "v": po.factory or "—"})
    meta += [
        {"k": "Qty", "v": f"{po.qty:,}"},
        {"k": "Stage", "v": po.stage},
        {"k": "Progress", "v": f"{progress}%"},
    ]

    return {
        "ref": po.order_no or "—",
        "title": po.style,
        "statusLabel": status_label,
        "statusTone": _STAGE_TONE.get(po.stage, "amber") if started else "neutral",
        "meta": meta,
        "orderLines": order_lines_out,
        "orderTotal": order_total,
        "sourcePurchaseOrder": {"publicId": str(purchase_order.public_id),
                                "poNo": purchase_order.po_no} if purchase_order else None,
        "started": started,
        "progress": progress,
        "canInspect": can_inspect,
        "inspected": inspected,
        "alert": alert,
        "stageNames": STAGES,
        "stages": overall["stages"],
        "lines": lines_out,
        "materials": [
            {"component": m["component"], "material": m["material"] or "—",
             "qty": m["qty_per_unit"] or "—", "cost": f"€{m['cost']:,.2f}"}
            for m in mats
        ],
    }


def start_production(session, *, tenant_id, public_id, stages, line_id=None):
    return repo.start_production(session, order_public_id=public_id, stages=stages,
                                 line_public_id=line_id)


def stage_action(session, *, action, public_id, days=None, worker=None, notes=None):
    from fastapi import HTTPException
    stage = repo.get_stage(session, public_id=public_id)
    if stage is not None and stage.name == "Tech Pack":
        if action in ("start", "complete", "resolve"):
            raise HTTPException(409, "Upload a tech pack to mark this stage Received.")
    if action == "start":
        return repo.start_stage(session, public_id=public_id)
    if action == "complete":
        stage = repo.complete_stage(session, public_id=public_id)
        # When this completes a whole production LINE (item), open that item's
        # inspection right away — independently of the other items on the order.
        if stage is not None:
            po = session.get(ProductionOrder, stage.order_id) if stage.order_id else None
            if po is not None and stage.line_id and _line_is_complete(session, stage.line_id):
                line = session.get(ProductionOrderLine, stage.line_id)
                if line is not None:
                    _open_line_inspection(session, tenant_id=stage.tenant_id,
                                          order_ref=po.order_no, line=line)
            elif po is not None and not stage.line_id and (po.progress or 0) >= 100:
                # Legacy order-level stages → one order-level inspection.
                from app.modules.quality import repository as qc_repo
                if not qc_repo.inspection_exists(session, order_ref=po.order_no):
                    qc_repo.create_inspection(session, tenant_id=stage.tenant_id,
                                              order_ref=po.order_no, stage="Final", aql="2.5",
                                              product=po.style, prod_qty=po.qty)
            if po is not None:
                from app.modules.sales import fulfillment
                fulfillment.receive_inspected_production(session, tenant_id=stage.tenant_id, order_ref=po.order_no)
        return stage
    if action == "extend":
        return repo.extend_stage(session, public_id=public_id, days=days or 0)
    if action == "assign":
        return repo.assign_stage(session, public_id=public_id, worker=worker or "")
    if action == "notes":
        return repo.notes_stage(session, public_id=public_id, notes=notes or "")
    if action == "resolve":
        return repo.resolve_stage(session, public_id=public_id)
    return None


def bom_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_bom_detail(session, public_id=public_id)
    if data is None:
        return None
    b, orders = data["bom"], data["orders"]
    return {
        "variant": "bomline",
        "ref": b.component,
        "title": b.component,
        "statusLabel": b.material or "Component",
        "statusTone": "navy",
        "meta": [
            {"k": "Style", "v": b.style or "—"},
            {"k": "Material", "v": b.material or "—"},
            {"k": "Qty / unit", "v": b.qty_per_unit or "—"},
            {"k": "Cost", "v": f"€{b.cost:,.2f}"},
        ],
        "tabs": ["Orders"],
        "bomOrders": [
            {"a": o["order_no"] or "—", "b": f"{o['qty']:,} units · {o['factory'] or '—'}",
             "c": f"{o['progress']}%", "tone": _STAGE_TONE.get(o["stage"], "neutral"),
             "s": o["stage"]}
            for o in orders
        ],
    }
