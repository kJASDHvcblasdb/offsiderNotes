from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Tuple, Optional

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import (
    JobTask,
    HandoverNote,
    EquipmentFault,
    RestockItem,
    StockItem,
    Bit, BitStatus,
)
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _chip_for_priority(p: int) -> str:
    # 0=critical, 1=high, 2=med, 3=low
    return {0: "chip-crit", 1: "chip-high", 2: "chip-med", 3: "chip-low"}.get(p or 2, "chip-med")


# ---- Fuel Watch helpers -------------------------------------------------------

def _fuelwatch_effective_priority(t: JobTask) -> int:
    """
    Compute an *effective* priority for a Fuel Watch task based on elapsed time and usage.
      - 0 (critical): current% <= critical%
      - 1 (high):     current% <= critical% + 10
      - else 2 (medium)
    Falls back to stored priority if required fields are missing.
    """
    ok = (
        t.is_fuel_watch
        and bool(t.started_at)
        and (t.tank_capacity_l or 0) > 0
        and (t.hourly_usage_lph or 0) >= 0
        and (t.start_percent is not None)
        and (t.critical_percent is not None)
    )
    if not ok:
        return t.priority or 2

    cap = float(t.tank_capacity_l or 0)
    start_pct = float(t.start_percent or 0)
    crit_pct = float(t.critical_percent or 25)
    use_lph = float(t.hourly_usage_lph or 0.0)

    # Hours elapsed
    hours = max(0.0, (datetime.utcnow() - t.started_at).total_seconds() / 3600.0)

    # Current litres
    start_l = cap * (start_pct / 100.0)
    curr_l = max(0.0, start_l - (hours * use_lph))
    curr_pct = 0.0 if cap <= 0 else (curr_l / cap) * 100.0

    if curr_pct <= crit_pct:
        return 0
    if curr_pct <= crit_pct + 10:
        return 1
    return 2


def _fuelwatch_snapshot(t: JobTask) -> Optional[Tuple[int, Optional[float]]]:
    """
    For UI: return (current_percent_int, hours_to_critical) for a Fuel Watch task.
    Returns None if task isn't a valid fuel watch.
    hours_to_critical: None means infinite (no burn) or cannot compute.
    """
    ok = (
        t.is_fuel_watch
        and bool(t.started_at)
        and (t.tank_capacity_l or 0) > 0
        and (t.hourly_usage_lph or 0) >= 0
        and (t.start_percent is not None)
        and (t.critical_percent is not None)
    )
    if not ok:
        return None

    cap = float(t.tank_capacity_l or 0)
    start_pct = float(t.start_percent or 0)
    crit_pct = float(t.critical_percent or 25)
    use_lph = float(t.hourly_usage_lph or 0.0)

    hours = max(0.0, (datetime.utcnow() - t.started_at).total_seconds() / 3600.0)
    start_l = cap * (start_pct / 100.0)
    curr_l = max(0.0, start_l - (hours * use_lph))
    curr_pct = 0.0 if cap <= 0 else (curr_l / cap) * 100.0

    if use_lph <= 0:
        # Not consuming — effectively infinite
        return (int(round(curr_pct)), None)

    crit_l = cap * (crit_pct / 100.0)
    if curr_l <= crit_l:
        hrs_to_crit = 0.0
    else:
        hrs_to_crit = (curr_l - crit_l) / use_lph

    return (int(round(curr_pct)), hrs_to_crit)


@router.get("", response_class=HTMLResponse)
def jobs_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    # Custom tasks (open first, by stored priority asc, newest last)
    tasks = db.scalars(
        select(JobTask).order_by(JobTask.is_closed, JobTask.priority, JobTask.id.desc())
    ).all()

    # Open handover notes with priority 0 or 1
    handover = db.scalars(
        select(HandoverNote)
        .where(HandoverNote.is_closed == False, HandoverNote.priority.in_([0, 1]))  # noqa: E712
        .order_by(HandoverNote.priority, HandoverNote.id.desc())
    ).all()

    # Open equipment faults (any priority, but we only surface P0/P1 in critical)
    faults = db.scalars(
        select(EquipmentFault)
        .where(EquipmentFault.is_resolved == False)  # noqa: E712
        .order_by(EquipmentFault.priority, EquipmentFault.id.desc())
    ).all()

    # High-priority restock (priority 1)
    restocks = db.scalars(
        select(RestockItem)
        .where(RestockItem.is_closed == False, RestockItem.priority == 1)  # noqa: E712
        .order_by(RestockItem.id.desc())
    ).all()

    # Low or critical stock (qty < min OR qty < buffer)
    stocks = db.scalars(select(StockItem)).all()
    lowcrit_stock = []
    for s in stocks:
        q = s.on_rig_qty or 0
        if q < (s.min_qty or 0) or q < (s.buffer_qty or 0):
            lowcrit_stock.append(s)

    # Bits that need attention
    bits = db.scalars(
        select(Bit).where(Bit.status.in_([BitStatus.NEEDS_RESHARPEN, BitStatus.VERY_USED]))
    ).all()

    # ---- Render helpers ------------------------------------------------------

    def _task_row(t: JobTask) -> str:
        # Use derived priority for Fuel Watch, else stored
        effective_pr = _fuelwatch_effective_priority(t) if t.is_fuel_watch else (t.priority if t.priority is not None else 2)
        chip = _chip_for_priority(effective_pr)
        status = "closed" if t.is_closed else "open"

        # Fuel Watch annotation (current %, hours to crit)
        extra = ""
        if t.is_fuel_watch:
            snap = _fuelwatch_snapshot(t)
            if snap:
                curr_pct, hrs_to_crit = snap
                hrs_txt = "∞" if hrs_to_crit is None else (f"{hrs_to_crit:.1f} h")
                extra = f" <small class='muted'>(Fuel Watch · {curr_pct}% now · to {t.critical_percent}% in {hrs_txt})</small>"

        notes_preview = (t.notes or "")
        if len(notes_preview) > 160:
            notes_preview = notes_preview[:160] + "…"

        return (
            f"<tr>"
            f"<td><span class='chip {chip}'>P{effective_pr}</span></td>"
            f"<td>{escape(t.title)}{extra}</td>"
            f"<td class='muted'>{escape(notes_preview)}</td>"
            f"<td>{status}</td>"
            f"<td>"
            f"<form method='post' action='/jobs/task/{t.id}/toggle' style='display:inline'>"
            f"<button class='btn' type='submit'>{'Reopen' if t.is_closed else 'Close'}</button></form> "
            f"<form method='post' action='/jobs/task/{t.id}/delete' style='display:inline'>"
            f"<button class='btn' type='submit' onclick='return confirm(\"Delete task #{t.id}?\")'>Delete</button></form>"
            f"</td>"
            f"</tr>"
        )

    # ---- Critical section ----------------------------------------------------

    crit_rows = []

    # Custom tasks: show open P0 first, then open P1 (using *effective* priority)
    for t in [x for x in tasks if not x.is_closed and (_fuelwatch_effective_priority(x) == 0)]:
        crit_rows.append(_task_row(t))
    for t in [x for x in tasks if not x.is_closed and (_fuelwatch_effective_priority(x) == 1)]:
        crit_rows.append(_task_row(t))

    # Handover P0/P1 (open)
    for h in handover:
        chip = _chip_for_priority(h.priority or 2)
        crit_rows.append(
            f"<tr>"
            f"<td><span class='chip {chip}'>P{h.priority}</span></td>"
            f"<td>Handover: {escape(h.title)}</td>"
            f"<td class='muted'>{escape((h.body or '')[:160] + ('…' if (h.body and len(h.body) > 160) else ''))}</td>"
            f"<td>open</td>"
            f"<td>"
            f"<form method='post' action='/handover/{h.id}/toggle' style='display:inline'>"
            f"<button class='btn' type='submit'>Close</button></form>"
            f"</td>"
            f"</tr>"
        )

    # Faults (surface P0/P1)
    for f in faults:
        chip = _chip_for_priority(f.priority or 2)
        if (f.priority or 2) <= 1 and not f.is_resolved:
            title = f.equipment_name or (f.equipment.name if f.equipment else "Equipment fault")
            crit_rows.append(
                f"<tr>"
                f"<td><span class='chip {chip}'>P{f.priority}</span></td>"
                f"<td>Fault: {escape(title)}</td>"
                f"<td class='muted'>{escape((f.description or '')[:160] + ('…' if (f.description and len(f.description) > 160) else ''))}</td>"
                f"<td>open</td>"
                f"<td><a class='btn' href='/equipment'>View</a></td>"
                f"</tr>"
            )

    # Restock (priority 1)
    for r in restocks:
        name = r.name
        if r.stock_item:
            name = f"{r.stock_item.name} ({r.stock_item.unit})"
        crit_rows.append(
            f"<tr>"
            f"<td><span class='chip chip-high'>P1</span></td>"
            f"<td>Restock: {escape(name)}</td>"
            f"<td class='muted'>{escape(f'{r.qty} {r.unit}')}</td>"
            f"<td>open</td>"
            f"<td><a class='btn' href='/restock'>View</a></td>"
            f"</tr>"
        )

    # Low/Critical stock
    for s in lowcrit_stock:
        crit_rows.append(
            f"<tr>"
            f"<td><span class='chip chip-high'>P1</span></td>"
            f"<td>Stock: {escape(s.name)}</td>"
            f"<td class='muted'>QTY {s.on_rig_qty or 0} | Min {s.min_qty or 0} | Buffer {s.buffer_qty or 0}</td>"
            f"<td>open</td>"
            f"<td><a class='btn' href='/stock'>View</a></td>"
            f"</tr>"
        )

    # Bits attention
    for b in bits:
        crit_rows.append(
            f"<tr>"
            f"<td><span class='chip chip-high'>P1</span></td>"
            f"<td>Bit attention: {escape(b.serial)}</td>"
            f"<td class='muted'>Status: {escape(b.status.value)}</td>"
            f"<td>open</td>"
            f"<td><a class='btn' href='/bits'>View</a></td>"
            f"</tr>"
        )

    critical_table = (
        "<p class='muted'>No critical items.</p>"
        if not crit_rows
        else "<table><thead><tr><th>Prio</th><th>Item</th><th>Details</th><th>Status</th><th></th></tr></thead>"
             f"<tbody>{''.join(crit_rows)}</tbody></table>"
    )

    # ---- Tasks section -------------------------------------------------------

    task_rows = [_task_row(t) for t in tasks]
    task_table = (
        "<p class='muted'>No custom tasks yet.</p>"
        if not task_rows
        else "<table><thead><tr><th>Prio</th><th>Title</th><th>Notes</th><th>Status</th><th></th></tr></thead>"
             f"<tbody>{''.join(task_rows)}</tbody></table>"
    )

    form_html = """
      <form method="post" action="/jobs/task/new" class="form" style="margin:.25rem 0 1rem;">
        <label>Title <input name="title" required></label>
        <label>Priority
          <select name="priority">
            <option value="0">Critical</option>
            <option value="1">High</option>
            <option value="2" selected>Medium</option>
            <option value="3">Low</option>
          </select>
        </label>
        <label>Notes <textarea name="notes" rows="3" placeholder="Optional details"></textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Add task</button>
        </div>
      </form>
    """

    content = (
        "<h2>Critical items</h2>" + critical_table +
        "<h2 style='margin-top:1rem;'>Tasks</h2>" + form_html + task_table
    )
    return wrap_page(title="Jobs", body_html=content, actor=actor, rig_title=rig)


@router.post("/task/new")
def task_new(
    actor: str = Depends(current_actor),
    title: str = Form(...),
    priority: int = Form(2),
    notes: str = Form(""),
    db=Depends(get_db),
):
    # Explicitly set both flags for legacy DBs with NOT NULL constraints
    t = JobTask(
        title=title,
        notes=(notes or None),
        priority=priority,
        is_closed=False,
        is_done=False,
    )
    db.add(t)
    db.commit()
    write_log(db, actor=actor or "crew", entity="jobtask", entity_id=t.id, action="create", summary=title)
    return RedirectResponse("/jobs", status_code=303)


@router.post("/task/{task_id}/toggle")
def task_toggle(
    task_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    t = db.get(JobTask, task_id)
    if t:
        t.is_closed = not t.is_closed
        db.commit()
        write_log(
            db,
            actor=actor or "crew",
            entity="jobtask",
            entity_id=task_id,
            action=("close" if t.is_closed else "reopen"),
            summary=t.title or "",
        )
    return RedirectResponse("/jobs", status_code=303)


@router.post("/task/{task_id}/delete")
def task_delete(
    task_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    t = db.get(JobTask, task_id)
    if t:
        ttl = t.title or ""
        db.delete(t)
        db.commit()
        write_log(db, actor=actor or "crew", entity="jobtask", entity_id=task_id, action="delete", summary=ttl)
    return RedirectResponse("/jobs", status_code=303)
