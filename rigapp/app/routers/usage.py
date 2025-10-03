from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import UsageLog, StockItem
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/usage", tags=["usage"])

@router.get("")
def usage_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    rows = []
    for u in db.scalars(select(UsageLog).order_by(UsageLog.id.desc())).all():
        rows.append(f"<tr><td>{u.id}</td><td>{u.item_name}</td><td>{u.qty} {u.unit}</td><td>{u.notes or ''}</td></tr>")
    table = "<p class='muted'>No usage logs.</p>" if not rows else (
        "<table><thead><tr><th>ID</th><th>Item</th><th>Qty</th><th>Notes</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )

    # stock select for convenience
    options = ["<option value=''>— none —</option>"]
    for s in db.scalars(select(StockItem).order_by(StockItem.name)).all():
        options.append(f"<option value='{s.id}'>{s.name} ({s.unit})</option>")

    form = f"""
      <form method="post" action="/usage/new" class="form">
        <label>Link stock item (optional)
          <select name="stock_item_id">{''.join(options)}</select>
        </label>
        <label>Item name (if not linking) <input name="item_name"></label>
        <label>Qty <input type="number" step="0.01" name="qty" required></label>
        <label>Unit <input name="unit" value="ea"></label>
        <label>Notes <textarea name="notes" rows="2"></textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Log usage</button>
        </div>
      </form>
    """
    body = form + "<hr style='margin:1rem 0'>" + table
    return wrap_page(title="Daily Usage", body_html=body, actor=actor, rig_title=rig)

@router.post("/new")
def usage_new(
    actor: str = Depends(current_actor),
    stock_item_id: str = Form(""),
    item_name: str = Form(""),
    qty: float = Form(...),
    unit: str = Form("ea"),
    notes: str = Form(""),
    db=Depends(get_db),
):
    linked = None
    if stock_item_id:
        linked = db.get(StockItem, int(stock_item_id))

    name = item_name or (linked.name if linked else "Unnamed")
    log = UsageLog(item_name=name, qty=qty, unit=unit, notes=(notes or None))
    db.add(log)

    # auto-decrement on-rig if linked
    if linked:
        linked.on_rig_qty = (linked.on_rig_qty or 0) - int(qty)
        if linked.on_rig_qty < 0:
            linked.on_rig_qty = 0

    db.commit()
    write_log(db, actor=actor or "crew", entity="usage", entity_id=log.id, action="create", summary=f"{name} -{qty}{unit}")
    return RedirectResponse("/usage", status_code=303)
