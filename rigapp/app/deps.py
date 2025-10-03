# rigapp/app/deps.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, List

from fastapi import HTTPException, Request

# We *try* to use the project's DB helper if it exposes load_rigs().
# If not present, we fall back to reading rigs.json directly.
def _load_rigs_fallback() -> List[Any]:
    """
    Read rigs from a rigs.json file without relying on db.py helpers.

    Search order:
      1) RIGS_JSON env var (absolute or relative path)
      2) rigapp/data/rigs.json (sibling to this file's package root)
      3) project-root/rigs.json (two levels up from this file)
    """
    # 1) Env var
    env_path = os.getenv("RIGS_JSON")
    if env_path:
        p = Path(env_path).expanduser()
        if not p.is_absolute():
            # resolve relative to project root (two levels up from app/)
            p = (Path(__file__).resolve().parents[2] / env_path).resolve()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return []

    # 2) rigapp/data/rigs.json
    pkg_root = Path(__file__).resolve().parents[1]  # rigapp/
    data_path = (pkg_root / "data" / "rigs.json")
    if data_path.exists():
        try:
            return json.loads(data_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    # 3) project-root/rigs.json
    project_root = Path(__file__).resolve().parents[2]
    root_path = (project_root / "rigs.json")
    if root_path.exists():
        try:
            return json.loads(root_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    return []


def _load_rigs() -> List[Any]:
    """
    Prefer .db.load_rigs() if available; otherwise use the fallback reader.
    """
    try:
        # Imported here to avoid hard dependency if db.py doesn't expose load_rigs
        from .db import load_rigs as _db_load_rigs  # type: ignore
        try:
            rigs = _db_load_rigs()  # type: ignore[call-arg]
            if isinstance(rigs, list) or isinstance(rigs, dict):
                return rigs  # type: ignore[return-value]
        except Exception:
            pass
    except Exception:
        pass
    return _load_rigs_fallback()


def _rigs_iterable() -> Iterable[str]:
    """
    Normalize whatever structure rigs.json has into an iterable of rig IDs.

    Supports:
      - [{"id": "RC3007", "name": "..."}, ...]
      - ["RC3007", "RC3006", ...]
      - {"RC3007": {...}, "RC3006": {...}}
    """
    rigs = _load_rigs()

    if isinstance(rigs, dict):
        # dict keyed by rig id
        return (str(k) for k in rigs.keys())

    if isinstance(rigs, list):
        if rigs and isinstance(rigs[0], dict):
            # list of dicts with {"id": "..."}
            return (str(r.get("id", "")).strip() for r in rigs if isinstance(r, dict))
        # list of strings
        return (str(x).strip() for x in rigs)

    return ()


def _rig_exists(rig_id: str) -> bool:
    rid = (rig_id or "").strip()
    if not rid:
        return False
    for existing in _rigs_iterable():
        if existing == rid:
            return True
    return False


async def require_rig_context(request: Request) -> str:
    """
    Dependency to ensure a valid rig context.

    Resolves the rig_id from:
      - Path params: /r/{rig_id}/...
      - Query string: ?rig=RC3007

    On success:
      - Sets request.state.rig_id
      - Returns the rig_id

    On failure:
      - Raises 404 with a helpful message
    """
    rig_id = (
        (request.path_params.get("rig_id") if hasattr(request, "path_params") else None)
        or request.query_params.get("rig")
        or ""
    ).strip()

    if not rig_id:
        raise HTTPException(status_code=404, detail="Rig id missing in path or query.")

    if not _rig_exists(rig_id):
        raise HTTPException(status_code=404, detail=f"Rig '{rig_id}' not found.")

    setattr(request.state, "rig_id", rig_id)
    return rig_id
