from __future__ import annotations
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from .models import AuditLog

def write_log(db: Session, actor: str, entity: str, entity_id: int | None, action: str, summary: str = "") -> None:
    db.add(AuditLog(actor=actor, entity=entity, entity_id=entity_id, action=action, summary=summary))
    db.commit()

def recent_logs(db: Session, limit: int = 10) -> List[AuditLog]:
    return db.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)).all()
