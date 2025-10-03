from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..db import get_db
from ..models import TravelLog
from ..auth import current_actor, current_rig_title, require_reader
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/travel", tags=["travel"])

@router.get("")
def travel_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    rows = []
    for r in db.scalars(select(TravelLog).order_by(TravelLog.id.desc())).all():
        when = r.started_at.strftime("%Y-%m-%d %H:%M") if r.started_at else ""
        rows.append(f"<tr><td>{r.id}</td><td>{r.person or ''}</td><td>{r.from_location} → {r.to_location}</td><td>{when}</td><td>{r.notes or ''}</td></tr>")
    table = "<p class='muted'>No travel logs yet.</p>" if not rows else (
        "<table><thead><tr><th>ID</th><th>Who</th><th>Route</th><th>Start</th><th>Notes</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    body = f"<p><a class='btn' href='/travel/new'>➕ New travel log</a></p>{table}"
    return wrap_page(title="Travel", body_html=body, actor=actor, rig_title=rig)

@router.get("/new")
def travel_new_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
):
    body = """
      <form method="post" action="/travel/new" class="form">
        <label>Who <input name="person" placeholder="Name"></label>
        <label>From <input name="from_location" required></label>
        <label>To <input name="to_location" required></label>
        <label>Start time <input name="started_at" type="datetime-local"></label>
        <label>End time <input name="ended_at" type="datetime-local"></label>
        <label>Notes <textarea name="notes" rows="3"></textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/travel">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title="New Travel Log", body_html=body, actor=actor, rig_title=rig)

@router.post("/new")
def travel_new(
    actor: str = Depends(current_actor),
    person: str = Form(""),
    from_location: str = Form(...),
    to_location: str = Form(...),
    started_at: str = Form(""),
    ended_at: str = Form(""),
    notes: str = Form(""),
    db=Depends(get_db),
):
    def _parse(x: str):
        if not x:
            return None
        # allow both "2025-10-01T12:34" and "2025-10-01 12:34"
        x = x.replace("T", " ")
        try:
            return datetime.fromisoformat(x)
        except Exception:
            return None

    t = TravelLog(
        person=person or None,
        from_location=from_location,
        to_location=to_location,
        started_at=_parse(started_at),
        ended_at=_parse(ended_at),
        notes=notes or None,
    )
    db.add(t)
    db.commit()
    write_log(db, actor=actor or "crew", entity="travel", entity_id=t.id, action="create", summary=f"{from_location} → {to_location}")
    return RedirectResponse("/travel", status_code=303)
