from __future__ import annotations

from html import escape
from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import HandoverNote
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/handover", tags=["handover"])


def _chip_for_priority(p: int) -> str:
    return {0: "chip-crit", 1: "chip-high", 2: "chip-med", 3: "chip-low"}.get(p or 2, "chip-med")


@router.get("", response_class=HTMLResponse)
def handover_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    # P0 → P1 → P2 → P3, open first then closed
    notes = db.scalars(
        select(HandoverNote).order_by(HandoverNote.is_closed, HandoverNote.priority, HandoverNote.id.desc())
    ).all()

    rows = []
    for n in notes:
        pr = n.priority if n.priority is not None else 2
        chip = _chip_for_priority(pr)
        rows.append(
            f"<tr>"
            f"<td><span class='chip {chip}'>P{pr}</span></td>"
            f"<td>{escape(n.title)}</td>"
            f"<td class='muted'>{escape((n.body or '')[:160] + ('…' if (n.body and len(n.body)>160) else ''))}</td>"
            f"<td>{'closed' if n.is_closed else 'open'}</td>"
            f"<td>"
            f"<form method='post' action='/handover/{n.id}/toggle' style='display:inline'>"
            f"<button class='btn' type='submit'>{'Reopen' if n.is_closed else 'Close'}</button></form> "
            f"<form method='post' action='/handover/{n.id}/delete' style='display:inline'>"
            f"<button class='btn' type='submit' onclick='return confirm(\"Delete handover note #{n.id}?\")'>Delete</button></form>"
            f"</td>"
            f"</tr>"
        )

    table = (
        "<p class='muted'>No handover notes yet.</p>"
        if not rows
        else (
            "<table><thead><tr><th>Prio</th><th>Title</th><th>Body</th><th>Status</th><th></th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    )

    form_html = """
      <form method="post" action="/handover/new" class="form" style="margin:.25rem 0 1rem;">
        <label>Title <input name="title" required></label>
        <label>Priority
          <select name="priority">
            <option value="0">Critical</option>
            <option value="1">High</option>
            <option value="2" selected>Medium</option>
            <option value="3">Low</option>
          </select>
        </label>
        <label>Body <textarea name="body" rows="4" placeholder="Optional details"></textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Add note</button>
        </div>
      </form>
    """

    content = form_html + table
    return wrap_page(title="Handover", body_html=content, actor=actor, rig_title=rig)


@router.post("/new")
def handover_new(
    actor: str = Depends(current_actor),
    title: str = Form(...),
    priority: int = Form(2),
    body: str = Form(""),
    db=Depends(get_db),
):
    n = HandoverNote(title=title, priority=priority, body=(body or None))
    db.add(n)
    db.commit()
    write_log(db, actor=actor or "crew", entity="handover", entity_id=n.id, action="create", summary=title)
    return RedirectResponse("/handover", status_code=303)


@router.post("/{note_id}/toggle")
def handover_toggle(
    note_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    n = db.get(HandoverNote, note_id)
    if n:
        n.is_closed = not n.is_closed
        db.commit()
        write_log(
            db,
            actor=actor or "crew",
            entity="handover",
            entity_id=note_id,
            action=("close" if n.is_closed else "reopen"),
            summary=n.title or "",
        )
    return RedirectResponse("/handover", status_code=303)


@router.post("/{note_id}/delete")
def handover_delete(
    note_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    n = db.get(HandoverNote, note_id)
    if n:
        ttl = n.title or ""
        db.delete(n)
        db.commit()
        write_log(db, actor=actor or "crew", entity="handover", entity_id=note_id, action="delete", summary=ttl)
    return RedirectResponse("/handover", status_code=303)
