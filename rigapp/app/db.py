# rigapp/app/db.py
from __future__ import annotations

from pathlib import Path
from typing import Dict

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base

_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

# cache of SessionLocal per rig
_SESSIONS: Dict[str, sessionmaker] = {}

def _safe_name(rig_id: str) -> str:
    return "".join(ch for ch in rig_id if ch.isalnum() or ch in ("-", "_")) or "default"

def _session_for_rig(rig_id: str) -> sessionmaker:
    rid = _safe_name(rig_id)
    if rid in _SESSIONS:
        return _SESSIONS[rid]
    db_path = _DATA_DIR / f"{rid}.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    # ensure tables exist for that rig
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    _SESSIONS[rid] = SessionLocal
    return SessionLocal

def get_db(request: Request):
    """Yield a rig-scoped session (based on offsider_rig cookie)."""
    rig_id = request.cookies.get("offsider_rig", "") or "default"
    SessionLocal = _session_for_rig(rig_id)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def ensure_db_initialized_with_seed() -> None:
    """
    No-op with per-rig DBs. Tables are created lazily on first use of each rig DB.
    """
    return None
