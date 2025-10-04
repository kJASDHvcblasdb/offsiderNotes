from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import Shroud, ShroudCondition
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/shrouds", tags=["shrouds"])

def _condition_options(selected: str | None = None) -> str:
    opts = []
    for c in ShroudCondition:
        sel = " selected" if selected and selected == c.value else ""
        opts.append(f"<option value='{c.value}'{sel}>{c.value}</option>")
    return "\n".join(opts)

def _render_new_form(err: str = "", name: str = "", notes: str = "", condition: str = "NEW") -> str:
    err_html = f"<p class='danger'>{escape(err)}</p>" if err else ""
    return f"""
      {err_html}
      <form method="post" action="/shrouds/new" class="form">
        <label>Name <input name="name" value="{escape(name)}" required></label>
        <label>Condition
          <select name="condition">
            {_condition_options(condition)}
          </select>
        </label>
        <label>Notes <textarea name="notes" rows="3">{escape(notes)}</textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/shrouds">Cancel</a>
        </div>
      </form>
    """

def _render_edit_form(s: Shroud, err: str = "") -> str:
    err_html = f"<p class='danger'>{escape(err)}</p>" if err else ""
    return f"""
      {err_html}
      <form method="post" action="/shrouds/{s.id}/edit" class="form">
        <label>Name <input name="name" value="{escape(s.name)}" required></label>
        <label>Condition
          <select name="condition">
            {_condition_options(s.condition.value)}
          </select>
        </label>
        <label>Notes <textarea name="notes" rows="3">{escape(s.notes or '')}</textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/shrouds">Cancel</a>
        </div>
      </form>
    """

@router.get("", response_class=HTMLResponse)
def shrouds_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    rows = []
    for s in db.scalars(select(Shroud).order_by(Shroud.name)).all():
        rows.append(
            f"<tr>"
            f"<td>{escape(s.name)}</td>"
            f"<td>{escape(s.condition.value)}</td>"
            f"<td class='muted'>{escape(s.notes or '')}</td>"
            f"<td>"
            f"<a class='btn' href='/shrouds/{s.id}/edit'>Edit</a>"
            f"<form method='post' action='/shrouds/{s.id}/delete' style='display:inline'>"
            f"<button class='btn' type='submit' onclick='return confirm(\"Delete {escape(s.name)}?\")'>Delete</button></form>"
            f"</td>"
            f"</tr>"
        )
    body = (
        "<a class='btn' href='/shrouds/new'>➕ Add shroud</a>" +
        ("<p class='muted'>No shrouds yet.</p>" if not rows else
         "<table><thead><tr><th>Name</th><th>Condition</th><th>Notes</th><th></th></tr></thead>"
         f"<tbody>{''.join(rows)}</tbody></table>")
    )
    return wrap_page(title="Shrouds", body_html=body, actor=actor, rig_title=rig)

@router.get("/new", response_class=HTMLResponse)
def shroud_new_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    err: str = "",
    name: str = "",
    notes: str = "",
    condition: str = "NEW",
):
    body = _render_new_form(err=err, name=name, notes=notes, condition=condition)
    return wrap_page(title="New Shroud", body_html=body, actor=actor, rig_title=rig)

@router.post("/new")
def shroud_new(
    actor: str = Depends(current_actor),
    name: str = Form(...),
    condition: str = Form("NEW"),
    notes: str = Form(""),
    db=Depends(get_db),
):
    s = Shroud(name=name.strip(), condition=ShroudCondition(condition), notes=(notes or None))
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        body = _render_new_form(err=f"A shroud named “{name}” already exists.", name=name, notes=notes, condition=condition)
        return HTMLResponse(wrap_page(title="New Shroud", body_html=body, actor=actor or "crew", rig_title=""), status_code=400)
    write_log(db, actor=actor or "crew", entity="shroud", entity_id=s.id, action="create", summary=s.name)
    return RedirectResponse("/shrouds", status_code=303)

@router.get("/{sid}/edit", response_class=HTMLResponse)
def shroud_edit_form(
    sid: int,
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
    err: str = "",
):
    s = db.get(Shroud, sid)
    if not s:
        return RedirectResponse("/shrouds", status_code=303)
    body = _render_edit_form(s, err=err)
    return wrap_page(title=f"Edit: {s.name}", body_html=body, actor=actor, rig_title=rig)

@router.post("/{sid}/edit")
def shroud_edit(
    sid: int,
    actor: str = Depends(current_actor),
    name: str = Form(...),
    condition: str = Form("NEW"),
    notes: str = Form(""),
    db=Depends(get_db),
):
    s = db.get(Shroud, sid)
    if not s:
        return RedirectResponse("/shrouds", status_code=303)

    s.name = name.strip()
    s.condition = ShroudCondition(condition)
    s.notes = notes or None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        body = _render_edit_form(s, err=f"A shroud named “{name}” already exists.")
        return HTMLResponse(wrap_page(title=f"Edit: {s.name}", body_html=body, actor=actor or "crew", rig_title=""), status_code=400)
    write_log(db, actor=actor or "crew", entity="shroud", entity_id=s.id, action="update", summary=s.name)
    return RedirectResponse("/shrouds", status_code=303)

@router.post("/{sid}/delete")
def shroud_delete(
    sid: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    s = db.get(Shroud, sid)
    if s:
        name = s.name
        db.delete(s)
        db.commit()
        write_log(db, actor=actor or "crew", entity="shroud", entity_id=sid, action="delete", summary=name or "")
    return RedirectResponse("/shrouds", status_code=303)
