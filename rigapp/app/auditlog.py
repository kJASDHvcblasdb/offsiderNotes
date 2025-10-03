from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from ..db import get_db
from ..models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])

@router.get("", response_class=HTMLResponse)
def audit_list(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(AuditLog).order_by(desc(AuditLog.when)).limit(50)
    ).all()
    # ultra-simple HTML
    items = []
    for r in rows:
        items.append(
            f"<li><a href='/audit/{r.id}'>#{r.id}</a> "
            f"<strong>{r.when}</strong> — <code>{r.actor}</code> — "
            f"{r.entity}[{r.entity_id}] <b>{r.action}</b><br>"
            f"<small>{(r.summary or '').replace('<','&lt;')}</small></li>"
        )
    html = f"""
    <html><body style="font-family: system-ui; max-width: 900px; margin: 2rem auto;">
      <h2>Audit Log (latest 50)</h2>
      <ul>{''.join(items) or '<li>No entries yet.</li>'}</ul>
      <p><a href="/">Back</a></p>
    </body></html>
    """
    return HTMLResponse(html)

@router.get("/{audit_id}", response_class=JSONResponse)
def audit_detail(audit_id: int, db: Session = Depends(get_db)):
    row = db.get(AuditLog, audit_id)
    if not row:
        raise HTTPException(status_code=404, detail="Audit entry not found")
    return {
        "id": row.id,
        "when": str(row.when),
        "actor": row.actor,
        "entity": row.entity,
        "entity_id": row.entity_id,
        "action": row.action,
        "summary": row.summary,
        "before_json": row.before_json,
        "after_json": row.after_json,
    }
