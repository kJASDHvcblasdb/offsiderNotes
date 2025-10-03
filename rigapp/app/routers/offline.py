from __future__ import annotations

import io
import csv
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import (
    Settings, StockItem, RestockItem, Bit, EquipmentFault, HandoverNote, JobTask,
    LocationNode, StockLocationLink, TravelLog, RefuelLog, UsageLog, Shroud
)
from ..ui import wrap_page

router = APIRouter(prefix="/offline", tags=["offline"])


def _rows_for(db, model):
    return db.scalars(select(model)).all()


def _to_dict(obj):
    d = {}
    for k, v in obj.__dict__.items():
        if k.startswith("_"):
            continue
        # serialize datetimes
        if hasattr(v, "isoformat"):
            try:
                v = v.isoformat()
            except Exception:
                pass
        # enums -> value
        val = getattr(v, "value", v)
        d[k] = val
    return d


@router.get("", response_class=HTMLResponse)
def offline_home(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
):
    body = """
      <p class="muted">Use the tools below when you need a guaranteed offline fallback.
         The web app will also work offline via a Service Worker and queue changes.</p>
      <div class="actions">
        <a class="btn" href="/offline/csv">⬇ Export CSV (zip)</a>
        <button class="btn" onclick="if(navigator.serviceWorker){navigator.serviceWorker.ready.then(r=>r.sync&&r.sync.register('sync-form-queue')).catch(()=>{});}">↻ Try sync queued changes</button>
      </div>
      <p class="muted">Tip: you can also “Add to Home Screen” to install the app.</p>
    """
    return wrap_page(title="Offline tools", body_html=body, actor=actor, rig_title=rig)


@router.get("/csv")
def export_csv_zip(
    ok: bool = Depends(require_reader),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED)

    tables = [
        ("settings.csv", Settings),
        ("stock_items.csv", StockItem),
        ("restock_items.csv", RestockItem),
        ("bits.csv", Bit),
        ("equipment_faults.csv", EquipmentFault),
        ("handover_notes.csv", HandoverNote),
        ("job_tasks.csv", JobTask),
        ("location_nodes.csv", LocationNode),
        ("stock_location_links.csv", StockLocationLink),
        ("travel_logs.csv", TravelLog),
        ("refuel_logs.csv", RefuelLog),
        ("usage_logs.csv", UsageLog),
        ("shrouds.csv", Shroud),
    ]

    for filename, model in tables:
        rows = _rows_for(db, model)
        if not rows:
            zf.writestr(filename, "")
            continue
        dicts = [_to_dict(r) for r in rows]
        fieldnames = sorted({k for d in dicts for k in d.keys()})
        sio = io.StringIO()
        w = csv.DictWriter(sio, fieldnames=fieldnames)
        w.writeheader()
        for d in dicts:
            w.writerow(d)
        zf.writestr(filename, sio.getvalue())

    manifest = f"exported_at,{datetime.utcnow().isoformat()}Z\nexported_by,{actor}\n"
    zf.writestr("manifest.txt", manifest)
    zf.close()
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="offline_export.csv.zip"'
        },
    )
