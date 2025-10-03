from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import AuditLog
from ..ui import wrap_page

router = APIRouter(prefix="/audit", tags=["audit"])

PAGE_SIZE = 100

@router.get("", response_class=HTMLResponse)
def audit_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor_name: str = Depends(current_actor),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    actor: Optional[str] = Query(None),
    entity: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    stmt = select(AuditLog).order_by(desc(AuditLog.created_at))
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(AuditLog.summary.ilike(like))

    total_rows = db.scalars(stmt).all()
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    rows = total_rows[start:end]

    filters_form = f"""
      <form method="get" action="/audit" class="form">
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:.5rem;">
          <label>Actor <input name="actor" value="{actor or ''}" placeholder="e.g., Cam"></label>
          <label>Entity <input name="entity" value="{entity or ''}" placeholder="stock/restock/fault/..."></label>
          <label>Search <input name="q" value="{q or ''}" placeholder="in summary"></label>
          <input type="hidden" name="page" value="1">
        </div>
        <div class="actions"><button class="btn" type="submit">Filter</button>
        <a class="btn" href="/audit">Clear</a></div>
      </form>
    """

    if not rows:
        table = "<p class='muted'>No audit entries match your filter.</p>"
    else:
        items = []
        for r in rows:
            when = r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""
            summary = (r.summary or "").replace("<", "&lt;")
            items.append(
                f"<tr>"
                f"<td>#{r.id}</td>"
                f"<td>{when}</td>"
                f"<td><code>{r.actor}</code></td>"
                f"<td>{r.entity}[{r.entity_id or ''}]</td>"
                f"<td><span class='badge'>{r.action}</span></td>"
                f"<td><small>{summary}</small></td>"
                f"<td><a class='btn' href='/audit/{r.id}'>View</a></td>"
                f"</tr>"
            )
        table = (
            "<table><thead><tr>"
            "<th>ID</th><th>When</th><th>Actor</th><th>What</th><th>Action</th><th>Summary</th><th></th>"
            "</tr></thead>"
            f"<tbody>{''.join(items)}</tbody></table>"
        )

    # pager
    pager = ""
    if total_rows:
        total_pages = (len(total_rows) + PAGE_SIZE - 1) // PAGE_SIZE
        if total_pages > 1:
            prev_link = f"<a class='btn' href='/audit?page={page-1}&actor={actor or ''}&entity={entity or ''}&q={q or ''}'>Prev</a>" if page > 1 else ""
            next_link = f"<a class='btn' href='/audit?page={page+1}&actor={actor or ''}&entity={entity or ''}&q={q or ''}'>Next</a>" if page < total_pages else ""
            pager = f"<div class='actions'><span class='muted'>Page {page} of {total_pages}</span> {prev_link} {next_link}</div>"

    body = filters_form + table + pager
    return wrap_page(title="Audit Log", body_html=body, actor=actor_name, rig_title=rig)


@router.get("/{audit_id}", response_class=JSONResponse)
def audit_detail(
    ok: bool = Depends(require_reader),
    audit_id: int = 0,
    db: Session = Depends(get_db),
):
    r = db.get(AuditLog, audit_id)
    if not r:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "actor": r.actor,
        "entity": r.entity,
        "entity_id": r.entity_id,
        "action": r.action,
        "summary": r.summary,
    }
