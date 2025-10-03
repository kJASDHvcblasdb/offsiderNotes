from __future__ import annotations
from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from . .db import get_db
from . .models import EquipmentFault, Equipment
from . .auth import current_actor
from . .audit import write_log

router = APIRouter(prefix="/faults", tags=["faults"])

@router.get("", response_class=HTMLResponse, name="faults_index")
def faults_index(db=Depends(get_db)):
    items = db.scalars(select(EquipmentFault).order_by(EquipmentFault.created_at.desc())).all()
    def row(f: EquipmentFault) -> str:
        name = f.equipment_name or (f.equipment.name if f.equipment else "")
        st = "open" if not f.is_resolved else "closed"
        return f"<tr><td>{name}</td><td>{(f.description or '')[:80]}</td><td>{st}</td></tr>"
    rows = "".join(row(f) for f in items)
    return HTMLResponse(f"""
      <h1>Equipment Faults</h1>
      <div class="actions"><a class="btn" href="/faults/new">Report fault</a></div>
      <table><thead><tr><th>Equipment</th><th>Issue</th><th>Status</th></tr></thead>
      <tbody>{rows or "<tr><td colspan='3' class='muted'>No faults.</td></tr>"}</tbody></table>
    """)

@router.get("/new", response_class=HTMLResponse, name="faults_new")
def faults_new_form(db=Depends(get_db)):
    eq = db.scalars(select(Equipment).order_by(Equipment.name)).all()
    options = "<option value=''>-- none --</option>" + "".join(f"<option value='{e.id}'>{e.name}</option>" for e in eq)
    return HTMLResponse(f"""
      <h1>Report fault</h1>
      <form method="post" action="/faults/new" class="form">
        <label>Equipment <select name="equipment_id">{options}</select></label>
        <label>Or equipment name <input name="equipment_name" maxlength="200" placeholder="If not listed"></label>
        <label>Description <textarea name="description" required rows="3"></textarea></label>
        <div class='actions'>
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/faults">Cancel</a>
        </div>
      </form>
    """)

@router.post("/new", name="faults_new_post")
def faults_new_post(
    equipment_id: int | None = Form(None),
    equipment_name: str | None = Form(None),
    description: str = Form(...),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    f = EquipmentFault(
        equipment_id=equipment_id if equipment_id else None,
        equipment_name=equipment_name,
        description=description,
        is_resolved=False,
    )
    db.add(f)
    db.commit()
    write_log(db, actor, "fault", f.id, "create", f"Reported fault: {description[:50]}")
    return RedirectResponse("/faults", status_code=303)
