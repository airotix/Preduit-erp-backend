"""Inventory business logic → frontend ScreenConfig."""
import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

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
        search="Search articles…", action="Stock receipt",
        filters=["Category", "Status"],
    )


def stock_article_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_article_stock(session, public_id=public_id)
    if data is None:
        return None
    product = data["product"]

    # Matrix column headers = the full Size catalog (ordered), plus any size
    # actually held that isn't in the catalog (appended at the end). Sizes with
    # no stock still appear as a column and render as 0.
    size_order: dict[str, int] = {
        s["value"]: (s["sort_order"] or 0) for s in data["all_sizes"]
    }
    for r in data["grid"]:
        sname = r["size"] or "—"
        if sname not in size_order:
            size_order[sname] = (r["size_order"] or 0) + 1000  # keep extras last
    sizes = sorted(size_order, key=lambda s: (size_order[s], s))

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
    """Persist the edited color × size grid: upsert colors, variants and stock."""
    tid = _tid(tenant_id)
    product = repo.get_product_by_public_id(session, public_id=public_id)
    if product is None:
        return None
    location_id = repo.primary_location_id(session, product_id=product.id)
    if location_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "No inventory location exists to store stock. Create a location first.")
    price, currency = repo.variant_defaults(session, product_id=product.id)

    for col in payload.colors:
        name = col.color.strip()
        if not name:
            continue
        color = repo.find_attr_value(session, attr_type="Color", value=name)
        if color is None:
            color = repo.create_color(session, tenant_id=tid, value=name, hex=col.hex)
        for cell in col.cells:
            size = repo.find_attr_value(session, attr_type="Size", value=cell.size)
            if size is None:
                continue
            variant = repo.find_or_create_variant(
                session, tenant_id=tid, product_id=product.id, color_id=color.id,
                size_id=size.id, price=price, currency=currency,
            )
            repo.set_variant_on_hand(session, tenant_id=tid, variant_id=variant.id,
                                     location_id=location_id, on_hand=cell.qty)

    return stock_article_detail(session, public_id=public_id)


def create_stock_receipt(session: Session, *, tenant_id: str | UUID, payload: StockReceiptCreate):
    tid = _tid(tenant_id)
    variant_id = repo.find_variant_id_by_sku(session, sku=payload.sku)
    if variant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No variant with SKU {payload.sku}")
    location_id = repo.find_location_id_by_name(session, name=payload.location)
    if location_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No location named {payload.location}")
    return repo.upsert_stock(
        session, tenant_id=tid, variant_id=variant_id, location_id=location_id,
        on_hand=payload.onHand, reserved=payload.reserved,
    )


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
        records=[{"status": r["status"]} for r in rows],
        search="Search transfers…", action="New transfer",
        filters=["Status", "Location"],
    )


def create_transfer(session: Session, *, tenant_id: str | UUID, payload: TransferCreate):
    tid = _tid(tenant_id)
    from_id = repo.find_location_id_by_name(session, name=payload.from_)
    to_id = repo.find_location_id_by_name(session, name=payload.to)
    return repo.create_transfer(session, tenant_id=tid, from_id=from_id, to_id=to_id, units=payload.units)


def set_transfer_status(session, *, public_id, status):
    return repo.set_transfer_status(session, public_id=public_id, status=status)


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
        search="Search alerts…", action="Create PO", filters=["Severity", "Category"],
    )


def create_alert(session: Session, *, tenant_id: str | UUID, payload: ReorderAlertCreate):
    tid = _tid(tenant_id)
    variant_id = repo.find_variant_id_by_sku(session, sku=payload.sku)
    return repo.create_alert(
        session, tenant_id=tid, variant_id=variant_id, sku=payload.sku,
        suggested=payload.suggested, supplier=payload.supplier,
    )
