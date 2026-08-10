"""Production business logic → frontend ScreenConfig."""
import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.production import repository as repo
from app.modules.production.dto import BomCreate, BomUpdate, ProductionOrderCreate
from app.presenters.screen import board_config, initials, list_config, text_cell

STAGES = ["Trims", "Lining", "Cutting", "Sewing", "Finishing", "Packed"]
_STAGE_TONE = {"Trims": "neutral", "Lining": "neutral", "Cutting": "navy",
               "Sewing": "amber", "Finishing": "green", "Packed": "green", "Completed": "green"}
_BOARD = [
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
    rows, total = repo.list_porders(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["order_no"] or "—", strong=True, mono=True),
            r["style"],
            r.get("customer") or "—",
            r["factory"] or "—",
            text_cell(f"{r['qty']:,}", align="right", mono=True),
            text_cell(r["stage"], badge=_STAGE_TONE.get(r["stage"], "neutral")),
            text_cell(f"{r['progress']}%", align="right", mono=True, strong=True),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Order"}, {"label": "Style"}, {"label": "Customer"},
                 {"label": "Factory"},
                 {"label": "Qty", "align": "right"}, {"label": "Stage"},
                 {"label": "Progress", "align": "right"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["stage"], "started": r.get("started", False),
                  "shippable": r.get("shippable", False), "shipped": r.get("shipped", False)}
                 for r in rows],
        search="Search production orders…", action="New order", filters=["Factory", "Stage"],
    )


def ship_order(session, *, tenant_id, public_id, carrier, eta, destination):
    """Log a completed production order as a shipment (Shipments module)."""
    from app.models.shipments import ShipmentLine
    from app.modules.shipments import repository as ship_repo
    po = repo.order_by_public(session, public_id)
    if po is None:
        return None
    s = ship_repo.create_shipment(session, tenant_id=_tid(tenant_id), order_ref=po.order_no,
                                  carrier=carrier, destination=destination)
    s.eta = eta
    session.add(ShipmentLine(tenant_id=s.tenant_id, shipment_id=s.id, sku=None,
                             description=po.style, qty=po.qty))
    session.flush()
    return s


def create_porder(session, *, tenant_id, payload: ProductionOrderCreate):
    return repo.create_porder(session, tenant_id=_tid(tenant_id), style=payload.style,
                              factory=payload.factory, qty=payload.qty)


def set_stage(session, *, public_id, status):
    return repo.set_stage(session, public_id=public_id, stage=status)


def board_screen(session: Session) -> dict:
    rows = repo.list_porders_all(session)
    columns = []
    for stage, title, accent, tone, meta_icon in _BOARD:
        cards = []
        for r in rows:
            if r["stage"] != stage:
                continue
            cards.append({
                "ref": r["order_no"] or "—",
                "title": r["style"],
                "sub": f"{r['qty']:,} units · {r['factory'] or '—'}",
                "meta": f"{r['progress']}%",
                "metaIcon": meta_icon,
                "av": initials(r["style"]),
                "tone": tone,
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
                  "material": r["material"], "cost": float(r["cost"]) if r["cost"] is not None else None}
                 for r in rows],
        search="Search BOMs…", action="New BOM", filters=["Style"],
    )


def _bom_fields(p) -> dict:
    return {"component": p.component, "style": p.style, "material": p.material, "cost": p.cost}


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
            "overdue": s.status != "Completed" and s.end_on is not None and s.end_on < today,
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
    active = next((s for s in stages if s.status != "Completed"), None)
    if active is None:
        return None
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
    started = len(stages) > 0
    completed = sum(1 for s in stages if s.status == "Completed")
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
    data = repo.get_porder_detail(session, public_id=public_id)
    if data is None:
        return None
    po, mats, stages = data["order"], data["materials"], data["stages"]
    sales_order, order_lines = data.get("sales_order"), data.get("order_lines") or []
    today = datetime.date.today()

    def _money(v) -> str:
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

    # One timeline per production line (style) → tabs on the detail page.
    lines_out = [
        {"publicId": str(ld["line"].public_id), "name": ld["line"].name,
         "qty": int(ld["line"].qty or 0), **_timeline(ld["stages"], today)}
        for ld in (data.get("lines") or [])
    ]

    meta = []
    if sales_order is not None:
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
        "started": started,
        "progress": progress,
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
    if action == "start":
        return repo.start_stage(session, public_id=public_id)
    if action == "complete":
        return repo.complete_stage(session, public_id=public_id)
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
