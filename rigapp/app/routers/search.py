from __future__ import annotations

from html import escape
from typing import Iterable

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, or_

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import (
    StockItem,
    RestockItem,
    Bit, BitStatus,
    EquipmentFault,
    HandoverNote,
    JobTask,
    LocationNode,
)
from ..ui import wrap_page

router = APIRouter(prefix="/search", tags=["search"])


def _hl(text: str, q: str, limit: int = 160) -> str:
    """
    Very light 'highlight': just return a trimmed/escaped preview within `limit`.
    (No JS; we keep it simple.)
    """
    if not text:
        return ""
    s = text
    idx = s.lower().find(q.lower())
    if idx == -1:
        s = s[:limit]
    else:
        start = max(0, idx - 40)
        end = min(len(s), idx + max(40, len(q)) + 40)
        s = s[start:end]
    s = s.replace("<", "&lt;")
    return (s + ("…" if len(s) == end else "")) if len(s) > limit else s


def _section(title: str, rows: Iterable[str]) -> str:
    rows = list(rows)
    if not rows:
        return ""
    return (
        f"<h3>{escape(title)}</h3>"
        "<table><thead><tr><th>Item</th><th>Details</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


@router.get("", response_class=HTMLResponse)
def search_page(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
    q: str = Query("", description="Search query"),
    scope_stock: bool = Query(True),
    scope_restock: bool = Query(True),
    scope_bits: bool = Query(True),
    scope_equipment: bool = Query(True),
    scope_handover: bool = Query(True),
    scope_jobs: bool = Query(True),
    scope_locations: bool = Query(True),
):
    # Form
    form_html = f"""
      <form method="get" action="/search" class="form"
            style="margin:.25rem 0 1rem; display:flex; gap:.9rem; align-items:flex-end; flex-wrap:wrap;">
        <div style="display:flex; flex-direction:column; min-width:260px; padding:.25rem .5rem .5rem 0;">
          <label>Query</label>
          <input name="q" value="{escape(q or '')}" placeholder="e.g., drill, bay 1, diesel, fault…">
        </div>

        <fieldset style="border:1px solid #eee; border-radius:8px; padding:.4rem .6rem;">
          <legend class="muted" style="font-size:.9rem;">Scopes</legend>
          <label><input type="checkbox" name="scope_stock" {"checked" if scope_stock else ""}> Stock</label>
          <label><input type="checkbox" name="scope_restock" {"checked" if scope_restock else ""}> Restock</label>
          <label><input type="checkbox" name="scope_bits" {"checked" if scope_bits else ""}> Bits</label>
          <label><input type="checkbox" name="scope_equipment" {"checked" if scope_equipment else ""}> Equipment Faults</label>
          <label><input type="checkbox" name="scope_handover" {"checked" if scope_handover else ""}> Handover</label>
          <label><input type="checkbox" name="scope_jobs" {"checked" if scope_jobs else ""}> Jobs</label>
          <label><input type="checkbox" name="scope_locations" {"checked" if scope_locations else ""}> Locations</label>
        </fieldset>

        <div class="actions" style="padding:.25rem .5rem .5rem 0;">
          <button class="btn" type="submit">Search</button>
          <a class="btn" href="/search">Reset</a>
        </div>
      </form>
    """

    if not q:
        return wrap_page(
            title="Search",
            body_html=form_html + "<p class='muted'>Enter a query and choose scopes to search.</p>",
            actor=actor,
            rig_title=rig,
        )

    like = f"%{q}%"

    sections_html: list[str] = []

    # --- Stock ----------------------------------------------------------------
    if scope_stock:
        stmt = select(StockItem).where(
            or_(
                StockItem.name.ilike(like),
                StockItem.unit.ilike(like),
                StockItem.location.ilike(like),
            )
        ).order_by(StockItem.name)
        items = db.scalars(stmt).all()
        rows = []
        for s in items:
            details = f"QTY {s.on_rig_qty or 0} · Min {s.min_qty or 0} · Buffer {s.buffer_qty or 0}"
            rows.append(
                f"<tr><td>{escape(s.name)}</td>"
                f"<td class='muted'>{_hl((s.location or ''), q)}</td>"
                f"<td><a class='btn' href='/stock'>View</a></td></tr>"
            )
        sections_html.append(_section("Stock", rows))

    # --- Restock --------------------------------------------------------------
    if scope_restock:
        stmt = select(RestockItem).where(
            or_(
                RestockItem.name.ilike(like),
                RestockItem.unit.ilike(like),
            )
        ).order_by(RestockItem.id.desc())
        items = db.scalars(stmt).all()
        rows = [
            f"<tr><td>{escape(r.name)}</td>"
            f"<td class='muted'>{escape(str(r.qty or 0))} {escape(r.unit or 'ea')}</td>"
            f"<td><a class='btn' href='/restock'>View</a></td></tr>"
            for r in items
        ]
        sections_html.append(_section("Restock", rows))

    # --- Bits -----------------------------------------------------------------
    if scope_bits:
        stmt = select(Bit).where(
            or_(
                Bit.serial.ilike(like),
                Bit.notes.ilike(like),
            )
        ).order_by(Bit.id.desc())
        items = db.scalars(stmt).all()
        rows = [
            f"<tr><td>{escape(b.serial)}</td>"
            f"<td class='muted'>Status: {escape(b.status.value)}</td>"
            f"<td><a class='btn' href='/bits'>View</a></td></tr>"
            for b in items
        ]
        sections_html.append(_section("Bits", rows))

    # --- Equipment faults -----------------------------------------------------
    if scope_equipment:
        stmt = select(EquipmentFault).where(
            or_(
                EquipmentFault.description.ilike(like),
                EquipmentFault.equipment_name.ilike(like),
            )
        ).order_by(EquipmentFault.id.desc())
        items = db.scalars(stmt).all()
        rows = [
            f"<tr><td>{escape(f.equipment_name or 'Equipment fault')}</td>"
            f"<td class='muted'>{_hl(f.description or '', q)}</td>"
            f"<td><a class='btn' href='/equipment'>View</a></td></tr>"
            for f in items
        ]
        sections_html.append(_section("Equipment faults", rows))

    # --- Handover notes -------------------------------------------------------
    if scope_handover:
        stmt = select(HandoverNote).where(
            or_(
                HandoverNote.title.ilike(like),
                HandoverNote.body.ilike(like),
            )
        ).order_by(HandoverNote.id.desc())
        items = db.scalars(stmt).all()
        rows = [
            f"<tr><td>{escape(h.title)}</td>"
            f"<td class='muted'>{_hl(h.body or '', q)}</td>"
            f"<td><a class='btn' href='/handover'>View</a></td></tr>"
            for h in items
        ]
        sections_html.append(_section("Handover", rows))

    # --- Jobs / tasks ---------------------------------------------------------
    if scope_jobs:
        stmt = select(JobTask).where(
            or_(
                JobTask.title.ilike(like),
                JobTask.notes.ilike(like),
            )
        ).order_by(JobTask.id.desc())
        items = db.scalars(stmt).all()
        rows = [
            f"<tr><td>{escape(t.title)}</td>"
            f"<td class='muted'>{_hl(t.notes or '', q)}</td>"
            f"<td><a class='btn' href='/jobs'>View</a></td></tr>"
            for t in items
        ]
        sections_html.append(_section("Jobs", rows))

    # --- Locations ------------------------------------------------------------
    if scope_locations:
        stmt = select(LocationNode).where(
            or_(
                LocationNode.name.ilike(like),
                LocationNode.kind.ilike(like),
                LocationNode.notes.ilike(like),
            )
        ).order_by(LocationNode.name)
        items = db.scalars(stmt).all()
        rows = [
            f"<tr><td>{escape(n.name)}</td>"
            f"<td class='muted'>{escape(n.kind or '')} {_hl(n.notes or '', q)}</td>"
            f"<td><a class='btn' href='/map/{n.id}/edit'>Open</a></td></tr>"
            for n in items
        ]
        sections_html.append(_section("Locations", rows))

    results_html = "".join([h for h in sections_html if h])

    if not results_html:
        results_html = "<p class='muted'>No results.</p>"

    page_html = form_html + results_html
    return wrap_page(title="Search", body_html=page_html, actor=actor, rig_title=rig)
