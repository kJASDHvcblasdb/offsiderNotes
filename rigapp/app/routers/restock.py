from __future__ import annotations

from html import escape
from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..db import get_db
from ..models import RestockItem, StockItem
from ..auth import require_reader, current_actor, current_rig_title
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/restock", tags=["restock"])


@router.get("", response_class=HTMLResponse)
def restock_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    items = db.scalars(
        select(RestockItem).order_by(RestockItem.is_closed, RestockItem.priority, RestockItem.id.desc())
    ).all()
    rows = []
    for r in items:
        name = r.name
        if r.stock_item:
            name = f"{r.stock_item.name} ({r.stock_item.unit})"
        status_badge = "<span class='badge badge-fixed'>closed</span>" if r.is_closed else "<span class='badge badge-open'>open</span>"
        pr_chip = {1: "chip-high", 2: "chip-med", 3: "chip-low"}.get(r.priority or 2, "chip-med")
        rows.append(
            f"<tr><td>{r.id}</td><td>{escape(name)}</td><td>{r.qty} {escape(r.unit or 'ea')}</td>"
            f"<td><span class='chip {pr_chip}'>P{r.priority}</span></td>"
            f"<td>{status_badge}</td>"
            f"<td>"
            f"<form method='post' action='/restock/{r.id}/toggle' style='display:inline'>"
            f"<button class='btn' type='submit'>{'Reopen' if r.is_closed else 'Close & fulfill'}</button></form> "
            f"<form method='post' action='/restock/{r.id}/delete' style='display:inline'>"
            f"<button class='btn' type='submit' onclick='return confirm(\"Delete restock #{r.id}?\")'>Delete</button></form>"
            f"</td></tr>"
        )
    table = "<p class='muted'>No restock entries.</p>" if not rows else (
        "<table><thead><tr><th>ID</th><th>Item</th><th>Qty</th><th>Prio</th><th>Status</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    body = (
        "<p class='actions'>"
        "<a class='btn' href='/restock/new'>➕ Add restock item</a> "
        "<a class='btn' href='/restock/suggest'>⚙️ Auto-suggest</a>"
        "</p>"
        + table
    )
    return wrap_page(title="Restock", body_html=body, actor=actor, rig_title=rig)


@router.get("/new", response_class=HTMLResponse)
def restock_new_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
    stock_item_id: int | None = Query(None),
    qty: int = Query(1),
    unit: str = Query("ea"),
    priority: int = Query(2),
):
    options = ["<option value=''>— link to stock item (optional) —</option>"]
    for s in db.scalars(select(StockItem).order_by(StockItem.name)).all():
        sel = " selected" if (stock_item_id and s.id == stock_item_id) else ""
        options.append(f"<option value='{s.id}'{sel}>{escape(s.name)} ({escape(s.unit or 'ea')})</option>")
    body = f"""
      <form method="post" action="/restock/new" class="form">
        <label>Link stock item
          <select name="stock_item_id">
            {''.join(options)}
          </select>
        </label>
        <label>Free-text item name <input name="name" placeholder="If not linking a stock item"></label>
        <label>Quantity <input type="number" name="qty" value="{qty}"></label>
        <label>Unit <input name="unit" value="{escape(unit or 'ea')}"></label>
        <label>Priority
          <select name="priority">
            <option value="1" {"selected" if priority==1 else ""}>High</option>
            <option value="2" {"selected" if priority==2 else ""}>Medium</option>
            <option value="3" {"selected" if priority==3 else ""}>Low</option>
          </select>
        </label>
        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/restock">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title="New Restock Item", body_html=body, actor=actor, rig_title=rig)


@router.post("/new")
def restock_new(
    actor: str = Depends(current_actor),
    stock_item_id: str = Form(""),
    name: str = Form(""),
    qty: int = Form(1),
    unit: str = Form("ea"),
    priority: int = Form(2),
    db=Depends(get_db),
):
    linked = None
    if stock_item_id:
        linked = db.get(StockItem, int(stock_item_id))
    item = RestockItem(
        stock_item_id=(linked.id if linked else None),
        name=name or (linked.name if linked else "Unnamed"),
        qty=qty,
        unit=unit or (linked.unit if linked else "ea"),
        priority=priority,
    )
    db.add(item)
    db.commit()
    write_log(db, actor=actor or "crew", entity="restock", entity_id=item.id, action="create", summary=item.name)
    return RedirectResponse("/restock", status_code=303)


@router.post("/{restock_id}/toggle")
def restock_toggle(
    restock_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    r = db.get(RestockItem, restock_id)
    if r:
        was_closed = r.is_closed
        r.is_closed = not r.is_closed

        # When moving from OPEN -> CLOSED, auto-fulfill linked stock
        if not was_closed and r.is_closed and r.stock_item:
            si = r.stock_item
            before_qty = si.on_rig_qty or 0
            si.on_rig_qty = before_qty + (r.qty or 0)
            db.commit()
            write_log(
                db,
                actor=actor or "crew",
                entity="stock",
                entity_id=si.id,
                action="restock-fulfill",
                summary=f"{si.name}: {before_qty} → {si.on_rig_qty} (+{r.qty})"
            )
        else:
            db.commit()

        write_log(
            db,
            actor=actor or "crew",
            entity="restock",
            entity_id=restock_id,
            action=("close" if r.is_closed else "reopen"),
            summary=r.name or "",
        )
    return RedirectResponse("/restock", status_code=303)


@router.post("/{restock_id}/delete")
def restock_delete(
    restock_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    r = db.get(RestockItem, restock_id)
    if r:
        name = r.name or ""
        db.delete(r)
        db.commit()
        write_log(db, actor=actor or "crew", entity="restock", entity_id=restock_id, action="delete", summary=name)
    return RedirectResponse("/restock", status_code=303)


# --------- Auto-suggest (Phase 1) --------------------------------------------

@router.get("/suggest", response_class=HTMLResponse)
def restock_suggest(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    # Simple rule: suggest to bring each item up to (min + buffer) if under that level
    stocks = db.scalars(select(StockItem).order_by(StockItem.name)).all()
    rows = []
    for s in stocks:
        qty = s.on_rig_qty or 0
        target = (s.min_qty or 0) + (s.buffer_qty or 0)
        if qty < target:
            need = target - qty
            rows.append(
                f"<tr>"
                f"<td>{escape(s.name)}</td>"
                f"<td>{qty}</td>"
                f"<td>{s.min_qty or 0}</td>"
                f"<td>{s.buffer_qty or 0}</td>"
                f"<td>{target}</td>"
                f"<td><strong>{need}</strong> {escape(s.unit or 'ea')}</td>"
                f"<td>"
                f"<form method='post' action='/restock/suggest/create' style='display:inline'>"
                f"<input type='hidden' name='stock_item_id' value='{s.id}'>"
                f"<input type='hidden' name='qty' value='{need}'>"
                f"<input type='hidden' name='unit' value='{escape(s.unit or 'ea')}'>"
                f"<button class='btn' type='submit'>Create restock</button>"
                f"</form>"
                f"</td>"
                f"</tr>"
            )
    body = (
        "<p class='muted'>Everything looks topped up. No suggestions right now.</p>"
        if not rows
        else (
            "<table><thead><tr>"
            "<th>Item</th><th>QTY</th><th>Min</th><th>Buffer</th><th>Target</th><th>Suggested</th><th></th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    )
    return wrap_page(title="Restock suggestions", body_html=body, actor=actor, rig_title=rig)


@router.post("/suggest/create")
def restock_suggest_create(
    actor: str = Depends(current_actor),
    stock_item_id: int = Form(...),
    qty: int = Form(...),
    unit: str = Form("ea"),
    db=Depends(get_db),
):
    s = db.get(StockItem, stock_item_id)
    if not s:
        return RedirectResponse("/restock/suggest", status_code=303)
    item = RestockItem(
        stock_item_id=s.id,
        name=s.name,
        qty=qty,
        unit=unit or (s.unit or "ea"),
        priority=2,  # default to Medium; can be changed later
    )
    db.add(item)
    db.commit()
    write_log(
        db,
        actor=actor or "crew",
        entity="restock",
        entity_id=item.id,
        action="create",
        summary=f"Suggested: {item.name} x{item.qty}{item.unit}",
    )
    return RedirectResponse("/restock", status_code=303)
