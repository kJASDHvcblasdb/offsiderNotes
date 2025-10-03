from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import Equipment, EquipmentFault
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="", tags=["equipment"])  # keep paths historically compatible

# --- Equipment ---------------------------------------------------------------

@router.get("/equipment")
def equipment_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    eq = db.scalars(select(Equipment).order_by(Equipment.name)).all()
    rows = []
    for e in eq:
        rows.append(
            f"<tr><td>{e.id}</td><td>{e.name}</td><td>{(e.description or '').replace('<','&lt;')}</td>"
            f"<td>"
            f"<a class='btn' href='/equipment/{e.id}/fault/new'>Add fault</a> "
            f"<form method='post' action='/equipment/{e.id}/delete' style='display:inline'>"
            f"<button class='btn' type='submit' onclick='return confirm(\"Delete equipment {e.name}?\")'>Delete</button>"
            f"</form>"
            f"</td></tr>"
        )
    table = "<p class='muted'>No equipment.</p>" if not rows else (
        "<table><thead><tr><th>ID</th><th>Name</th><th>Description</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    body = f"<p><a class='btn' href='/equipment/new'>➕ Add equipment</a> <a class='btn' href='/faults'>View faults</a></p>{table}"
    return wrap_page(title="Equipment", body_html=body, actor=actor, rig_title=rig)

@router.get("/equipment/new")
def equipment_new_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
):
    body = """
      <form method="post" action="/equipment/new" class="form">
        <label>Name <input name="name" required></label>
        <label>Description <textarea name="description" rows="3"></textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/equipment">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title="New Equipment", body_html=body, actor=actor, rig_title=rig)

@router.post("/equipment/new")
def equipment_new(
    actor: str = Depends(current_actor),
    name: str = Form(...),
    description: str = Form(""),
    db=Depends(get_db),
):
    e = Equipment(name=name, description=(description or None))
    db.add(e)
    db.commit()
    write_log(db, actor=actor or "crew", entity="equipment", entity_id=e.id, action="create", summary=name)
    return RedirectResponse("/equipment", status_code=303)

@router.post("/equipment/{eq_id}/delete")
def equipment_delete(
    eq_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    e = db.get(Equipment, eq_id)
    if e:
        name = e.name
        db.delete(e)
        db.commit()
        write_log(db, actor=actor or "crew", entity="equipment", entity_id=eq_id, action="delete", summary=name or "")
    return RedirectResponse("/equipment", status_code=303)

# --- Faults -------------------------------------------------------------------

@router.get("/equipment/{eq_id}/fault/new")
def fault_new_form(
    eq_id: int,
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    e = db.get(Equipment, eq_id)
    if not e:
        return RedirectResponse("/equipment", status_code=303)
    body = f"""
      <h2>New fault for: {e.name}</h2>
      <form method="post" action="/equipment/{eq_id}/fault/new" class="form">
        <label>Description <textarea name="description" rows="3" required></textarea></label>
        <label>Priority
          <select name="priority">
            <option value="1">High</option>
            <option value="2" selected>Medium</option>
            <option value="3">Low</option>
          </select>
        </label>
        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/equipment">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title="New Fault", body_html=body, actor=actor, rig_title=rig)

@router.post("/equipment/{eq_id}/fault/new")
def fault_new(
    eq_id: int,
    actor: str = Depends(current_actor),
    description: str = Form(...),
    priority: int = Form(2),
    db=Depends(get_db),
):
    e = db.get(Equipment, eq_id)
    if not e:
        return RedirectResponse("/equipment", status_code=303)
    f = EquipmentFault(equipment_id=e.id, equipment_name=e.name, description=description, is_resolved=False, priority=priority)
    db.add(f)
    db.commit()
    write_log(db, actor=actor or "crew", entity="fault", entity_id=f.id, action="create", summary=description[:100])
    return RedirectResponse("/faults", status_code=303)

@router.get("/faults")
def faults_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    faults = db.scalars(select(EquipmentFault).order_by(EquipmentFault.is_resolved, EquipmentFault.priority, EquipmentFault.id.desc())).all()
    rows = []
    for f in faults:
        status_badge = "<span class='badge badge-fixed'>resolved</span>" if f.is_resolved else "<span class='badge badge-open'>open</span>"
        # Attention if open + high priority
        if (not f.is_resolved) and (f.priority == 1):
            status_badge = "<span class='badge badge-attention'>attention</span>"
        pr_chip = {1:"chip-high",2:"chip-med",3:"chip-low"}.get(f.priority or 2, "chip-med")
        rows.append(
            f"<tr><td>{f.id}</td><td>{f.equipment_name or (f.equipment.name if f.equipment else '')}</td>"
            f"<td>{(f.description or '').replace('<','&lt;')}</td>"
            f"<td>{status_badge}</td>"
            f"<td><span class='chip {pr_chip}'>P{f.priority}</span></td>"
            f"<td>"
            f"<form method='post' action='/faults/{f.id}/toggle' style='display:inline'>"
            f"<button class='btn' type='submit'>{'Reopen' if f.is_resolved else 'Resolve'}</button>"
            f"</form> "
            f"<form method='post' action='/faults/{f.id}/delete' style='display:inline'>"
            f"<button class='btn' type='submit' onclick='return confirm(\"Delete fault #{f.id}?\")'>Delete</button>"
            f"</form>"
            f"</td></tr>"
        )
    table = "<p class='muted'>No faults recorded.</p>" if not rows else (
        "<table><thead><tr><th>ID</th><th>Equipment</th><th>Description</th><th>Status</th><th>Priority</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    body = f"<p><a class='btn' href='/equipment'>Back to Equipment</a></p>{table}"
    return wrap_page(title="Equipment Faults", body_html=body, actor=actor, rig_title=rig)

@router.post("/faults/{fault_id}/toggle")
def faults_toggle(
    fault_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    f = db.get(EquipmentFault, fault_id)
    if f:
        f.is_resolved = not f.is_resolved
        db.commit()
        write_log(db, actor=actor or "crew", entity="fault", entity_id=fault_id, action=("resolve" if f.is_resolved else "reopen"), summary=f.description[:100] if f.description else "")
    return RedirectResponse("/faults", status_code=303)

@router.post("/faults/{fault_id}/delete")
def faults_delete(
    fault_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    f = db.get(EquipmentFault, fault_id)
    if f:
        summary = f.description or ""
        db.delete(f)
        db.commit()
        write_log(db, actor=actor or "crew", entity="fault", entity_id=fault_id, action="delete", summary=summary[:100])
    return RedirectResponse("/faults", status_code=303)
