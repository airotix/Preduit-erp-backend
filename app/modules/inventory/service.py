"""Inventory business logic → frontend ScreenConfig."""
import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.modules.inventory import repository as repo
from app.modules.inventory.dto import (
    LocationCreate, LocationUpdate, MatrixUpdate, ReorderAlertCreate,
    StockReceiptCreate, TransferCreate,
)
from app.presenters.screen import list_config, text_cell

_LOC_TONE = {"Warehouse": "navy", "Retail": "neutral"}
_TRANSFER_TONE = {"Draft": "neutral", "In transit": "amber", "Received": "green", "Cancelled": "red"}
_SEVERITY_TONE = {"Critical": "red", "Low": "amber", "Watch": "neutral"}


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


def _variant_name(title: str | None, color: str | None, size: str | None, sku: str) -> tuple[str, str]:
    parts = [p for p in [title, color, size] if p]
    return (" · ".join(parts) if parts else sku, sku)


# ---------- Stock levels (one row per article) ----------

def _stock_status_cell(available: int):
    if available <= 0:
        return text_cell("Out of stock", badge="red")
    if available < 40:
        return text_cell("Low", badge="amber")
    return text_cell("Healthy", badge="green")


def stock_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_stock(session, limit=limit, offset=offset)
    def _money(v):
        return f"€{v:,.2f}" if v is not None else "—"

    grid = []
    for r in rows:
        on_hand = r["on_hand"] or 0
        reserved = r["reserved"] or 0
        available = on_hand - reserved
        colors = r["colors"] or 0
        grid.append([
            text_cell(r["title"], avatar=True, sub=r["category"] or "—"),
            text_cell(r["sku"] or "—", mono=True),
            text_cell(f"{colors} color{'' if colors == 1 else 's'}", align="right", mono=True),
            text_cell(f"{on_hand:,}", align="right", mono=True),
            text_cell(f"{available:,}", align="right", mono=True, strong=True),
            text_cell(_money(r["retail"]), align="right", mono=True),
            text_cell(_money(r["online"]), align="right", mono=True),
            text_cell(_money(r["wholesale"]), align="right", mono=True),
            _stock_status_cell(available),
        ])
    return list_config(
        columns=[
            {"label": "Article"}, {"label": "SKU ID"},
            {"label": "Colors", "align": "right"},
            {"label": "On hand", "align": "right"},
            {"label": "Available", "align": "right"},
            {"label": "Retail Price", "align": "right"},
            {"label": "Online Price", "align": "right"},
            {"label": "Wholesale Price", "align": "right"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        # "Category" isn't its own visible column (it only appears as the
        # Article cell's sub-text) — surfaced here so the Category filter has
        # real data to filter on.
        records=[{"category": r["category"]} for r in rows],
        search="Search articles…", action="Stock receipt",
        filters=["Category", "Status"],
    )


def stock_article_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_article_stock(session, public_id=public_id)
    if data is None:
        return None
    product = data["product"]

    # Always show the standard apparel scale, even before variants exist.
    size_order: dict[str, int] = {
        s["value"]: (s["sort_order"] or 0) for s in data["all_sizes"]
    }
    for r in data["grid"]:
        sname = r["size"] or "—"
        if sname not in size_order:
            size_order[sname] = (r["size_order"] or 0) + 1000  # keep extras last
    standard_sizes = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL", "6XL", "7XL"]
    sizes = standard_sizes + sorted(
        (s for s in size_order if s not in standard_sizes), key=lambda s: (size_order[s], s))

    # Group by color, summing on-hand per size, then align to the size list.
    by_color: dict[str, dict] = {}
    for r in data["grid"]:
        cname = r["color"] or "—"
        c = by_color.setdefault(cname, {
            "name": cname, "hex": r["hex"] or "#CBD1DC", "qty": {},
        })
        sname = r["size"] or "—"
        c["qty"][sname] = c["qty"].get(sname, 0) + (r["on_hand"] or 0)

    colors = []
    for c in by_color.values():
        cells = [c["qty"].get(s, 0) for s in sizes]
        colors.append({"name": c["name"], "hex": c["hex"],
                       "cells": cells, "total": sum(cells)})
    colors.sort(key=lambda c: (-c["total"], c["name"]))

    on_hand = sum(c["total"] for c in colors)
    reserved = sum(r["reserved"] or 0 for r in data["locations"])
    available = on_hand - reserved

    locations = [
        {"location": loc["location"], "on_hand": loc["on_hand"] or 0,
         "reserved": loc["reserved"] or 0,
         "available": (loc["on_hand"] or 0) - (loc["reserved"] or 0)}
        for loc in data["locations"]
    ]

    return {
        "variant": "stockarticle",
        "ref": (product.season or "Stock"),
        "title": product.title,
        "statusLabel": _stock_status_cell(available)["t"],
        "statusTone": _stock_status_cell(available)["badge"],
        "meta": [
            {"k": "On hand", "v": f"{on_hand:,}"},
            {"k": "Reserved", "v": f"{reserved:,}"},
            {"k": "Available", "v": f"{available:,}"},
            {"k": "Colors", "v": str(len(colors))},
        ],
        "tabs": ["Colors & sizes", "By location"],
        "stock": {"sizes": sizes, "colors": colors, "locations": locations},
    }


def save_article_matrix(session: Session, *, tenant_id: str | UUID, public_id: str,
                        payload: MatrixUpdate) -> dict | None:
    from app.modules.inventory.matrix import save_matrix
    product = save_matrix(session, tenant_id=tenant_id, public_id=public_id, payload=payload)
    return stock_article_detail(session, public_id=public_id) if product is not None else None


def search_stock_products(session: Session, *, q: str, limit: int = 10) -> list[dict]:
    # Empty query lists all in-stock items (click-to-browse); typing filters.
    return repo.search_stock_products(session, q=(q or "").strip(), limit=limit)


def _active_location(session, tenant_id, name):
    from app.models.inventory import Location
    rows = session.scalars(select(Location).where(Location.tenant_id == tenant_id,
        Location.name == name.strip(), Location.is_deleted.is_(False)).limit(2)).all()
    if len(rows) != 1:
        raise HTTPException(400, f"Select a valid, unambiguous inventory location: {name}.")
    return rows[0].id


def create_stock_receipt(session: Session, *, tenant_id: str | UUID, payload: StockReceiptCreate):
    """Receive stock: for each article/colour/size line, add qty to on-hand at
    the location (creating the variant + stock row if needed)."""
    tid = _tid(tenant_id)
    location_id = _active_location(session, tid, payload.location)
    from app.models.catalog import Product
    planned = []
    for line in payload.lines:
        if not (line.color or "").strip() or not (line.size or "").strip():
            raise HTTPException(400, f"Select a color and size for {line.name}.")
        products = session.scalars(select(Product).where(Product.tenant_id == tid,
            Product.title == line.name.strip(), Product.is_deleted.is_(False)).limit(2)).all()
        if len(products) != 1:
            raise HTTPException(400, f"Select a valid, unambiguous catalog product: {line.name}.")
        planned.append((line, products[0]))
    from app.modules.sales import fulfillment
    waiting_orders = fulfillment.lock_waiting_orders(session, tenant_id=tid)
    received = 0
    for l, product in planned:
        price, currency = repo.variant_defaults(session, product_id=product.id)
        color = (repo.find_attr_value(session, attr_type="Color", value=l.color.strip(), tenant_id=tid)
                 or repo.create_color(session, tenant_id=tid, value=l.color.strip(), hex=None))
        size = (repo.find_attr_value(session, attr_type="Size", value=l.size.strip(), tenant_id=tid)
                or repo.create_size(session, tenant_id=tid, value=l.size.strip()))
        variant = repo.find_or_create_variant(
            session, tenant_id=tid, product_id=product.id, color_id=color.id,
            size_id=size.id, price=price, currency=currency)
        repo.add_stock(session, tenant_id=tid, variant_id=variant.id,
                       location_id=location_id, qty=int(l.qty or 0))
        received += int(l.qty or 0)
    if received:
        fulfillment.refresh_waiting_orders(session, waiting_orders)
    return received


# ---------- Locations ----------

def locations_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_locations(session, limit=limit, offset=offset)
    grid = []
    for r in rows:
        util = (
            f"{round((r['onhand'] or 0) / r['capacity'] * 100)}%"
            if r["capacity"] else "—"
        )
        grid.append([
            text_cell(r["name"], avatar=True, sub=r["code"] or ""),
            text_cell(r["kind"], badge=_LOC_TONE.get(r["kind"], "neutral")),
            r["region"] or "—",
            text_cell(str(r["skus"] or 0), align="right", mono=True),
            text_cell(util, align="right", mono=True, strong=True),
        ])
    return list_config(
        columns=[
            {"label": "Location"}, {"label": "Type"}, {"label": "Region"},
            {"label": "SKUs", "align": "right"},
            {"label": "Utilization", "align": "right"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"name": r["name"], "code": r["code"], "type": r["kind"],
                  "region": r["region"], "capacity": r["capacity"]} for r in rows],
        search="Search locations…", action="New location", filters=["Type"],
    )


def _location_fields(p) -> dict:
    return {"name": p.name, "code": p.code, "kind": p.type,
            "region": p.region, "capacity": p.capacity}


def create_location(session: Session, *, tenant_id: str | UUID, payload: LocationCreate):
    return repo.create_location(session, tenant_id=_tid(tenant_id), **_location_fields(payload))


def update_location(session: Session, *, public_id: str, payload: LocationUpdate):
    return repo.update_location(session, public_id=public_id, **_location_fields(payload))


# ---------- Transfers ----------

def transfers_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_transfers(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["transfer_no"] or "—", strong=True, mono=True),
            r["from_name"] or "—",
            r["to_name"] or "—",
            text_cell(f"{r['units']:,}", align="right", mono=True),
            text_cell(r["status"], badge=_TRANSFER_TONE.get(r["status"], "neutral")),
            r["eta"] or "—",
        ]
        for r in rows
    ]
    return list_config(
        columns=[
            {"label": "Transfer"}, {"label": "From"}, {"label": "To"},
            {"label": "Units", "align": "right"}, {"label": "Status"}, {"label": "ETA"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        # "Location" covers both ends of the transfer — a row matches the
        # filter if either its From or its To location is picked.
        records=[{"status": r["status"], "location": [r["from_name"], r["to_name"]]} for r in rows],
        search="Search transfers…", action="New transfer",
        filters=["Status", "Location"],
    )


def create_transfer(session: Session, *, tenant_id: str | UUID, payload: TransferCreate):
    tid = _tid(tenant_id)
    from_id = _active_location(session, tid, payload.from_)
    to_id = _active_location(session, tid, payload.to)
    if from_id == to_id:
        raise HTTPException(400, "Source and destination must be different locations.")
    from types import SimpleNamespace
    from app.modules.sales import fulfillment
    from app.models.inventory import StockLevel
    quantities = {}
    for line in payload.lines:
        if not (line.color or "").strip() or not (line.size or "").strip():
            raise HTTPException(400, f"Select a color and size for {line.name}.")
        variant = fulfillment.variant_for(session, SimpleNamespace(tenant_id=tid), line)
        if variant is None or variant.status == "Archived":
            raise HTTPException(400, f"No active catalog variant for {line.name} / {line.color} / {line.size}.")
        quantities[variant.id] = quantities.get(variant.id, 0) + line.qty
    # Lock both locations in a stable order, then validate the whole transfer.
    levels = session.scalars(select(StockLevel).where(StockLevel.tenant_id == tid,
        StockLevel.variant_id.in_(quantities), StockLevel.location_id.in_([from_id, to_id]))
        .order_by(StockLevel.id).with_for_update().execution_options(populate_existing=True)).all()
    source = {level.variant_id: level for level in levels if level.location_id == from_id}
    for variant_id, qty in quantities.items():
        level = source.get(variant_id)
        if level is None or level.on_hand - level.reserved < qty:
            raise HTTPException(409, "Transfer quantity exceeds available stock after sales reservations.")
    lines = [{"name": l.name, "color": l.color, "size": l.size, "sku": l.sku, "qty": l.qty}
             for l in payload.lines]
    trf = repo.create_transfer(session, tenant_id=tid, from_id=from_id, to_id=to_id, lines=lines)
    for vid, qty in quantities.items():
        repo.adjust_stock(session, tenant_id=tid, variant_id=vid, location_id=from_id, on_hand_delta=-qty)
        repo.adjust_stock(session, tenant_id=tid, variant_id=vid, location_id=to_id, on_hand_delta=qty)
    trf.status = "Received"
    session.flush()
    return trf


def location_options(session: Session) -> list[dict]:
    return repo.location_options(session)


def set_transfer_status(session, *, public_id, status):
    from app.models.inventory import StockTransfer
    transfer = session.scalar(select(StockTransfer).where(StockTransfer.public_id == public_id,
        StockTransfer.is_deleted.is_(False)).with_for_update())
    if transfer and transfer.status == "Received" and status != "Received":
        raise HTTPException(409, "Stock has already moved. Create a reverse transfer to move it back.")
    return repo.set_transfer_status(session, public_id=public_id, status=status)


# ---------- Stock movement (called from sales / shipments / procurement) ----------

def reserve_order_stock(session, *, tenant_id, lines) -> None:
    """Reserve stock for a new sales order — best-effort per line (a line whose
    variant can't be resolved is simply skipped, never blocking the order)."""
    tid = _tid(tenant_id)
    for ln in lines:
        vid = repo.resolve_variant_id(session, sku=ln.get("sku"), name=ln.get("name"),
                                      color=ln.get("color"), size=ln.get("size"))
        qty = int(ln.get("qty") or 0)
        if vid is None or qty <= 0:
            continue
        loc = repo.variant_stock_location_id(session, variant_id=vid)
        if loc is not None:
            repo.adjust_stock(session, tenant_id=tid, variant_id=vid, location_id=loc,
                              reserved_delta=qty)


def relieve_order_stock(session, *, tenant_id, lines) -> None:
    """Relieve stock when an order ships: reduce on-hand and release the
    reservation for each line."""
    tid = _tid(tenant_id)
    for ln in lines:
        vid = repo.resolve_variant_id(session, sku=ln.get("sku"), name=ln.get("name"),
                                      color=ln.get("color"), size=ln.get("size"))
        qty = int(ln.get("qty") or 0)
        if vid is None or qty <= 0:
            continue
        loc = repo.variant_stock_location_id(session, variant_id=vid)
        if loc is not None:
            repo.adjust_stock(session, tenant_id=tid, variant_id=vid, location_id=loc,
                              on_hand_delta=-qty, reserved_delta=-qty)


def receive_stock_lines(session, *, tenant_id, lines) -> None:
    """Increment stock when goods are received against a PO (per line)."""
    tid = _tid(tenant_id)
    loc = repo.first_location_id(session)
    for ln in lines:
        vid = repo.resolve_variant_id(session, sku=ln.get("sku"), name=ln.get("name"),
                                      color=ln.get("color"), size=ln.get("size"))
        qty = int(ln.get("qty") or 0)
        if vid is None or qty <= 0:
            continue
        dest = repo.variant_stock_location_id(session, variant_id=vid) or loc
        if dest is not None:
            repo.adjust_stock(session, tenant_id=tid, variant_id=vid, location_id=dest,
                              on_hand_delta=qty)


# ---------- Reorder alerts ----------

def alerts_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_alerts(session, limit=limit, offset=offset)
    def _money(v):
        return f"€{v:,.2f}" if v is not None else "—"

    grid = []
    for r in rows:
        name, sku = _variant_name(r["title"], r["color"], r["size"], r["sku"])
        avail = r["available"] or 0
        avail_cell = text_cell(str(avail), align="right", mono=True)
        if avail == 0:
            avail_cell["color"] = "#C0392B"
        grid.append([
            text_cell(name, avatar=True, sub=r["color"] or "—"),
            text_cell(r["sku"] or "—", mono=True),
            avail_cell,
            text_cell(str(r["reorder_point"] or 0), align="right", mono=True),
            text_cell(str(r["suggested"] or 0), align="right", mono=True, strong=True),
            text_cell(_money(r["retail"]), align="right", mono=True),
            text_cell(_money(r["online"]), align="right", mono=True),
            text_cell(_money(r["wholesale"]), align="right", mono=True),
            text_cell(r["severity"], badge=_SEVERITY_TONE.get(r["severity"], "neutral")),
        ])
    return list_config(
        columns=[
            {"label": "Variant"}, {"label": "SKU ID"},
            {"label": "Available", "align": "right"},
            {"label": "Reorder pt", "align": "right"},
            {"label": "Suggested", "align": "right"},
            {"label": "Retail Price", "align": "right"},
            {"label": "Online Price", "align": "right"},
            {"label": "Wholesale Price", "align": "right"}, {"label": "Severity"},
        ],
        rows=grid, total=total,
        records=[{"category": r["category"]} for r in rows],
        search="Search alerts…", action="Create PO", filters=["Severity", "Category"],
    )


def create_alert(session: Session, *, tenant_id: str | UUID, payload: ReorderAlertCreate):
    tid = _tid(tenant_id)
    variant_id = repo.find_variant_id_by_sku(session, sku=payload.sku)
    return repo.create_alert(
        session, tenant_id=tid, variant_id=variant_id, sku=payload.sku,
        suggested=payload.suggested, supplier=payload.supplier,
    )
