from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Bit, BitStatus, Shroud
from ..auth import require_reader, current_actor, current_rig_title
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/bits", tags=["bits"])

STATUSES = [s.value for s in BitStatus]

@router.get("", response_class=HTMLResponse)
def bits_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db: Session = Depends(get_db),
):
    bits = db.scalars(select(Bit).order_by(Bit.id.desc())).all()
    rows = []
    for b in bits:
        status_text = b.status.value if hasattr(b.status, "value") else str(b.status)
        notes_preview = (b.notes or "").strip()
        if len(notes_preview) > 60:
            notes_preview = notes_preview[:57] + "…"
        shroud = b.shroud.name if b.shroud else ""
        rows.append(
            f"<tr>"
            f"<td>{b.serial or ''}</td>"
            f"<td>{status_text or ''}</td>"
            f"<td>{b.life_meters_expected or ''}</td>"
            f"<td>{b.life_meters_used or ''}</td>"
            f"<td>{shroud}</td>"
            f"<td><small>{notes_preview}</small></td>"
            f"<td><a class='btn' href='/bits/{b.id}'>View</a></td>"
            f"</tr>"
        )
    table = (
        "<p class='muted'>No bits yet.</p>"
        if not rows
        else "<table><thead><tr>"
             "<th>Serial</th><th>Status</th><th>Expected life (m)</th><th>Used (m)</th><th>Shroud</th><th>Notes</th><th></th>"
             "</tr></thead>"
             f"<tbody>{''.join(rows)}</tbody></table>"
    )
    body = f"""
      <p><a class="btn" href="/bits/new">Add bit</a> <a class="btn" href="/shrouds">Shrouds</a></p>
      {table}
    """
    return wrap_page(title="Bits", body_html=body, actor=actor, rig_title=rig)

@router.get("/new", response_class=HTMLResponse)
def bits_new_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db: Session = Depends(get_db),
):
    options = "".join([f"<option value='{s}'>{s}</option>" for s in STATUSES])
    shroud_opts = ["<option value=''>— None —</option>"]
    for s in db.scalars(select(Shroud).order_by(Shroud.name)).all():
        shroud_opts.append(f"<option value='{s.id}'>{s.name}</option>")
    body = f"""
      <form method="post" action="/bits/new" class="form">
        <label>Serial <input name="serial" required maxlength="120"></label>
        <label>Status
          <select name="status" required>
            {options}
          </select>
        </label>
        <label>Expected life (m) <input type="number" name="life_expected" value="0"></label>
        <label>Used (m) <input type="number" name="life_used" value="0"></label>
        <label>Shroud
          <select name="shroud_id">{''.join(shroud_opts)}</select>
        </label>
        <label>Notes <textarea name="notes" rows="3"></textarea></label>
        <div class='actions'>
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/bits">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title="New Bit", body_html=body, actor=actor, rig_title=rig)

@router.post("/new")
def bits_new(
    actor: str = Depends(current_actor),
    serial: str = Form(...),
    status: str = Form(...),
    life_expected: int = Form(0),
    life_used: int = Form(0),
    shroud_id: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    # Parse enum safely
    try:
        status_enum = BitStatus(status)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid status")

    b = Bit(
        serial=serial,
        status=status_enum,
        life_meters_expected=life_expected or None,
        life_meters_used=life_used or 0,
        shroud_id=(int(shroud_id) if shroud_id else None),
        notes=(notes or None),
    )
    db.add(b)
    db.commit()
    write_log(db, actor=actor or "crew", entity="bit", entity_id=b.id, action="create", summary=serial)
    return RedirectResponse("/bits", status_code=303)

@router.get("/{bit_id}", response_class=HTMLResponse)
def bit_detail(
    bit_id: int,
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db: Session = Depends(get_db),
):
    b = db.get(Bit, bit_id)
    if not b:
        return RedirectResponse("/bits", status_code=303)
    status_text = b.status.value if hasattr(b.status, "value") else str(b.status)
    shroud = b.shroud.name if b.shroud else "—"
    notes_html = (b.notes or "").replace("<", "&lt;").replace("\n", "<br>")
    body = f"""
      <div class="cards">
        <div class="card"><strong>Serial</strong><span class="muted">{b.serial}</span></div>
        <div class="card"><strong>Status</strong><span class="muted">{status_text}</span></div>
        <div class="card"><strong>Expected life (m)</strong><span class="muted">{b.life_meters_expected or '—'}</span></div>
        <div class="card"><strong>Used (m)</strong><span class="muted">{b.life_meters_used or 0}</span></div>
        <div class="card"><strong>Shroud</strong><span class="muted">{shroud}</span></div>
      </div>
      <h2>Notes</h2>
      <p>{notes_html or '<span class="muted">None</span>'}</p>
      <p><a class="btn" href="/bits">Back to list</a></p>
    """
    return wrap_page(title=f"Bit #{b.id}", body_html=body, actor=actor, rig_title=rig)
