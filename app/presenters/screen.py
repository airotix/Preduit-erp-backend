"""Backend-for-Frontend presenter (plan §1).

Maps normalized entities into the frontend's presentation-shaped ScreenConfig
(`ListConfig`/`Cell`, see frontend/src/lib/screen-types.ts) so the existing
generic renderers work unchanged while modules migrate off the mocks.
"""
from typing import Any

Cell = Any  # str | int | dict


def text_cell(value: str, *, sub: str | None = None, strong: bool = False,
              mono: bool = False, badge: str | None = None,
              align: str | None = None, avatar: bool = False) -> Cell:
    cell: dict[str, Any] = {"t": value}
    if sub:
        cell["sub"] = sub
    if strong:
        cell["strong"] = True
    if mono:
        cell["mono"] = True
    if badge:
        cell["badge"] = badge
    if align:
        cell["align"] = align
    if avatar:
        cell["avatar"] = True
    return cell


def list_config(*, columns: list[dict], rows: list[list[Cell]], total: int,
                search: str | None = None, action: str | None = None,
                filters: list[str] | None = None,
                ids: list[str] | None = None,
                records: list[dict] | None = None) -> dict:
    return {
        "kind": "list",
        "search": search,
        "action": action,
        "filters": filters or [],
        "columns": columns,
        "rows": rows,
        "total": total,
        # Stable public ids parallel to rows[] — lets a clicked row open its
        # real detail record (instead of relying on array position).
        "ids": ids or [],
        # Raw editable field values parallel to rows[] (present on editable
        # screens) — prefill the Edit form without a second fetch.
        "records": records or [],
    }


def board_config(columns: list[dict]) -> dict:
    """Kanban board (see frontend BoardConfig)."""
    return {"kind": "board", "columns": columns}


def initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()
