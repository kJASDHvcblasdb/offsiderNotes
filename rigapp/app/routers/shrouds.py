# rigapp/app/routers/shrouds.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..db import get_db
from ..models import Shroud, ShroudCondition
from ..auth import require_reader, current_actor, current_rig_title

router = APIRouter(prefix="/shrouds", tags=["shrouds"])

def _wrap(title: str, body: str, rig: str, actor: str) -> HTMLResponse:
    html = f"""
    <html><head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <link rel="stylesheet" href="/static/style.css"><title>{title}</title>
    </head><body class="container">
      <h1>{title}</h1>
      <p class="muted">Rig: <strong>{rig}</strong> · Crew: <strong>{actor}</strong></p>
      {body}
    </body></html>
    """
    return HTMLResponse(html)

@router.get("", response_class=HTMLResponse)
def shrouds_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    rows = []
    for s in db.scalars(select(Shroud).order_by(Shroud.name)).all():
        rows.append(f"<tr><td>{s.id}</td><td>{s.name}</td><td>{s.condition.value}</td><td>{s.notes or ''}</td></tr>")
    table = "<p class='muted'>No shrouds yet.</p>" if not rows else (
        "<table><thead><tr><th>ID</th><th>Name</th><th>Condition</th><th>Notes</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    body = f"<p><a class='btn' href='/shrouds/new'>➕ Add shroud</a></p>{table}"
    return _wrap("Shrouds", body, rig, actor)

@router.get("/new", response_class=HTMLResponse)
def shroud_new_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
):
    options = "".join([f"<option value='{c.value}'>{c.value}</option>" for c in ShroudCondition])
    body = f"""
      <form method="post" action="/shrouds/new" class="form">
        <label>Name/code <input name="name" required maxlength="120"></label>
        <label>Condition
          <select name="condition">{options}</select>
        </label>
        <label>Notes <textarea name="notes" rows="3"></textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/shrouds">Cancel</a>
        </div>
      </form>
    """
    return _wrap("New Shroud", body, rig, actor)

@router.post("/new")
def shroud_new(
    name: str = Form(...),
    condition: str = Form("NEW"),
    notes: str = Form(""),
    db=Depends(get_db),
):
    s = Shroud(name=name, condition=ShroudCondition(condition), notes=notes or None)
    db.add(s)
    db.commit()
    return RedirectResponse("/shrouds", status_code=303)
