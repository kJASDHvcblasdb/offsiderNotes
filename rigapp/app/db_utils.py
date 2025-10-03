from __future__ import annotations

import shutil
from pathlib import Path
from datetime import datetime

from .db import _DATA_DIR

def safe_db_snapshot(rig_id: str = "default") -> Path:
    """
    Make a simple file-level copy of the rig database into /data/snapshots/.
    SQLite allows file copies while readers/writers are active; this is a pragmatic fallback.
    """
    src = Path(_DATA_DIR) / f"{rig_id}.db"
    snap_dir = Path(_DATA_DIR) / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dst = snap_dir / f"{rig_id}-{ts}.db"
    shutil.copy2(src, dst)
    return dst
