from __future__ import annotations

from datetime import datetime
from html import escape
from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..db import get_db
from ..models import RefuelLog, JobTask
from ..auth import require_reader, current_actor, current_rig_title
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/refuel", tags=["refuel"])

# ---- Index -------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def refuel_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    rows = []
    for r in db.scalars(select(RefuelLog).order_by(RefuelLog.id.desc())).all():
        extra = []
        if r.tank_capacity_l:
            extra.append(f"Cap {r.tank_capacity_l:.0f}L")
        if r.target_percent is not None:
            extra.append(f"Target {r.target_percent}%")
        if r.est_added_litres:
            extra.append(f"Est +{r.est_added_litres:.0f}L")
        extra_txt = (" · " + ", ".join(extra)) if extra else ""
        rows.append(
            f"<tr><td>{r.id}</td><td>{escape(r.fuel_type or '')}</td><td>{r.amount_litres or ''}</td>"
            f"<td>{escape(r.before_after_note or '')}</td><td>{escape(r.notes or '')}{escape(extra_txt)}</td></tr>"
        )
    table = "<p class='muted'>No refuels yet.</p>" if not rows else (
        "<table><thead><tr><th>ID</th><th>Fuel</th><th>Litres</th><th>When</th><th>Notes</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    body = f"""
      <p class="actions">
        <a class="btn" href="/refuel/new">➕ New refuel</a>
        <a class="btn" href="/refuel/calc">🧮 Refuel calculator</a>
      </p>
      {table}
    """
    return wrap_page(title="Refuel", body_html=body, actor=actor, rig_title=rig)

# ---- New refuel --------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
def refuel_new_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    tank_capacity_l: float | None = Query(None),
    target_percent: int | None = Query(None),
    est_added_litres: float | None = Query(None),
):
    # Allow pre-fill from calculator
    cap_val = "" if tank_capacity_l is None else f"{tank_capacity_l:.0f}"
    trg_val = "" if target_percent is None else f"{int(target_percent)}"
    est_val = "" if est_added_litres is None else f"{est_added_litres:.0f}"

    body = f"""
      <form method="post" action="/refuel/new" class="form">
        <label>Fuel type <input name="fuel_type" placeholder="Diesel"></label>
        <label>Amount (litres) <input type="number" step="0.01" name="amount_litres" required></label>
        <label>When note <input name="before_after_note" placeholder="Before/After service"></label>
        <label>Notes <textarea name="notes" rows="3"></textarea></label>

        <h3 class="muted" style="margin-top:.75rem;">(Optional) Calculator context</h3>
        <label>Tank capacity (L) <input name="tank_capacity_l" type="number" step="1" value="{cap_val}"></label>
        <label>Target percent (%) <input name="target_percent" type="number" step="1" min="0" max="100" value="{trg_val}"></label>
        <label>Estimated added (L) <input name="est_added_litres" type="number" step="1" value="{est_val}"></label>

        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/refuel">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title="New Refuel", body_html=body, actor=actor, rig_title=rig)

@router.post("/new")
def refuel_new(
    actor: str = Depends(current_actor),
    fuel_type: str = Form(""),
    amount_litres: float = Form(...),
    before_after_note: str = Form(""),
    notes: str = Form(""),
    tank_capacity_l: float | None = Form(None),
    target_percent: int | None = Form(None),
    est_added_litres: float | None = Form(None),
    db=Depends(get_db),
):
    r = RefuelLog(
        fuel_type=(fuel_type or None),
        amount_litres=amount_litres,
        before_after_note=(before_after_note or None),
        notes=(notes or None),
        tank_capacity_l=tank_capacity_l,
        target_percent=target_percent,
        est_added_litres=est_added_litres,
    )
    db.add(r)
    db.commit()
    write_log(db, actor=actor or "crew", entity="refuel", entity_id=r.id, action="create", summary=f"{amount_litres}L {fuel_type or ''}")
    return RedirectResponse("/refuel", status_code=303)

# ---- Calculator --------------------------------------------------------------

@router.get("/calc", response_class=HTMLResponse)
def refuel_calc_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    tank_capacity_l: float = Query(1000.0, description="Tank capacity in litres"),
    current_percent: int = Query(40, ge=0, le=100),
    target_percent: int = Query(80, ge=0, le=100),
    hourly_usage_lph: float = Query(20.0, description="Estimated hourly usage (L/h)"),
    critical_percent: int = Query(25, ge=0, le=100, description="Flip to critical at/below this %"),
):
    # Clamp & compute
    current_percent = max(0, min(100, int(current_percent)))
    target_percent = max(0, min(100, int(target_percent)))
    critical_percent = max(0, min(100, int(critical_percent)))
    tank_capacity_l = float(max(1.0, tank_capacity_l))
    hourly_usage_lph = float(max(0.0, hourly_usage_lph))

    current_l = tank_capacity_l * (current_percent / 100.0)
    target_l = tank_capacity_l * (target_percent / 100.0)
    add_l = max(0.0, target_l - current_l)

    # Hours until reaching critical (from NOW)
    # If current <= critical, it's already critical (0 hrs)
    if hourly_usage_lph <= 0:
        hours_to_critical = float("inf")
    else:
        if current_percent <= critical_percent:
            hours_to_critical = 0.0
        else:
            litres_until_critical = tank_capacity_l * ((current_percent - critical_percent) / 100.0)
            hours_to_critical = litres_until_critical / hourly_usage_lph

    # Build a result block
    hrs_txt = "∞" if hours_to_critical == float("inf") else f"{hours_to_critical:.1f} h"
    prefill_url = (
        f"/refuel/new?"
        f"tank_capacity_l={int(tank_capacity_l)}&target_percent={target_percent}&est_added_litres={int(add_l)}"
    )

    body = f"""
      <form method="get" action="/refuel/calc" class="form">
        <label>Tank capacity (L) <input type="number" step="1" name="tank_capacity_l" value="{int(tank_capacity_l)}" required></label>
        <label>Current percent (%) <input type="number" step="1" name="current_percent" value="{current_percent}" min="0" max="100" required></label>
        <label>Target percent (%) <input type="number" step="1" name="target_percent" value="{target_percent}" min="0" max="100" required></label>
        <label>Hourly usage (L/h) <input type="number" step="0.1" name="hourly_usage_lph" value="{hourly_usage_lph}"></label>
        <label>Critical at (%) <input type="number" step="1" name="critical_percent" value="{critical_percent}" min="0" max="100"></label>
        <div class="actions">
          <button class="btn" type="submit">Recalculate</button>
          <a class="btn" href="/refuel">Cancel</a>
        </div>
      </form>

      <h3>Result</h3>
      <p><strong>Add ≈ {int(add_l)} L</strong> to reach {target_percent}% (from {current_percent}%) on a {int(tank_capacity_l)} L tank.</p>
      <p>At {hourly_usage_lph:.1f} L/h, time until {critical_percent}% is <strong>{hrs_txt}</strong>.</p>

      <div class="actions">
        <a class="btn" href="{prefill_url}">Prefill “New refuel”</a>

        <form method="post" action="/refuel/watch" style="display:inline;">
          <input type="hidden" name="tank_capacity_l" value="{int(tank_capacity_l)}">
          <input type="hidden" name="start_percent" value="{current_percent}">
          <input type="hidden" name="critical_percent" value="{critical_percent}">
          <input type="hidden" name="hourly_usage_lph" value="{hourly_usage_lph}">
          <button class="btn" type="submit">Create Fuel Watch job</button>
        </form>
      </div>
    """
    return wrap_page(title="Refuel calculator", body_html=body, actor=actor, rig_title=rig)

# ---- Fuel Watch -> creates a time-based JobTask ------------------------------

@router.post("/watch")
def create_fuel_watch(
    actor: str = Depends(current_actor),
    tank_capacity_l: float = Form(...),
    start_percent: int = Form(...),
    critical_percent: int = Form(...),
    hourly_usage_lph: float = Form(...),
    db=Depends(get_db),
):
    # Store as a JobTask; priority will be derived on the Jobs page
    title = "Fuel Watch"
    notes = f"Auto-escalates when predicted fuel ≤ {critical_percent}%."

    jt = JobTask(
        title=title,
        notes=notes,
        priority=2,                # initial; UI will escalate based on live calc
        is_closed=False,
        is_done=False,
        is_fuel_watch=True,
        tank_capacity_l=float(tank_capacity_l),
        start_percent=int(start_percent),
        critical_percent=int(critical_percent),
        hourly_usage_lph=float(hourly_usage_lph),
        started_at=datetime.utcnow(),
    )
    db.add(jt)
    db.commit()

    write_log(
        db,
        actor=actor or "crew",
        entity="jobtask",
        entity_id=jt.id,
        action="create-fuelwatch",
        summary=f"Cap {int(tank_capacity_l)}L; start {int(start_percent)}%; crit {int(critical_percent)}%; {hourly_usage_lph:.1f} L/h",
    )
    return RedirectResponse("/jobs", status_code=303)
