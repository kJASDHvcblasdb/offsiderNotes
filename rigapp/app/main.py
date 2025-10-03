from __future__ import annotations

from pathlib import Path
from typing import List
import asyncio
from contextlib import suppress

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from starlette.middleware.base import BaseHTTPMiddleware

from .db import get_db, ensure_db_initialized_with_seed
from .models import (
    Settings,
    StockItem,
    RestockItem,
    Bit,
    BitStatus,
    EquipmentFault,
    HandoverNote,
    JobTask,
    LocationNode,
)
from .auth import (
    router as auth_router,
    current_actor,
    current_rig_id,
    current_rig_title,
)

from .routers.stock import router as stock_router
from .routers.restock import router as restock_router
from .routers.bits import router as bits_router
from .routers.usage import router as usage_router
from .routers.shrouds import router as shrouds_router
from .routers.equipment import router as equipment_router
from .routers.travel import router as travel_router
from .routers.refuel import router as refuel_router
from .routers.handover import router as handover_router
from .routers.auditlog import router as audit_router
from .routers.jobs import router as jobs_router
from .routers.map import router as map_router

from .audit import recent_logs
from . import scheduler
from .ui import wrap_page

app = FastAPI(title="Rig App", version="0.1")

# ---- Static ------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

class AutoCSSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        body_bytes = None
        try:
            if hasattr(resp, "body"):
                b = resp.body
                if isinstance(b, (bytes, bytearray)):
                    body_bytes = bytes(b)
                elif isinstance(b, str):
                    body_bytes = b.encode(getattr(resp, "charset", "utf-8"), "ignore")
        except Exception:
            body_bytes = None
        if body_bytes is None:
            return resp

        ctype = (resp.headers.get("content-type") or "").lower()
        looks_html = False
        try:
            probe = body_bytes[:256].decode(getattr(resp, "charset", "utf-8"), "ignore").lower()
            looks_html = ("<html" in probe) or ("<body" in probe)
        except Exception:
            pass
        if ("text/html" not in ctype) and not looks_html:
            return resp

        try:
            charset = getattr(resp, "charset", "utf-8")
            text = body_bytes.decode(charset, "ignore")
        except Exception:
            return resp

        lower = text.lower()
        has_head = "<head" in lower
        has_css = "static/style.css" in lower

        if has_head and has_css:
            if "text/html" not in ctype:
                return HTMLResponse(text, status_code=resp.status_code, headers=dict(resp.headers))
            return resp

        head_snippet = (
            "<head>"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<link rel="stylesheet" href="/static/style.css">'
            "</head>"
        )

        if not has_head:
            idx_html = lower.find("<html")
            if idx_html != -1:
                idx_close = lower.find(">", idx_html)
                if idx_close != -1:
                    text = text[: idx_close + 1] + head_snippet + text[idx_close + 1 :]
                else:
                    text = "<html>" + head_snippet + text
            else:
                idx_body = lower.find("<body")
                if idx_body != -1:
                    text = text[:idx_body] + head_snippet + text[idx_body:]
                else:
                    text = head_snippet + text
        else:
            text = text.replace(
                "<head>",
                "<head>"
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<link rel="stylesheet" href="/static/style.css">',
                1,
            )

        return HTMLResponse(text, status_code=resp.status_code, headers=dict(resp.headers))

app.add_middleware(AutoCSSMiddleware)

# ---- Startup -----------------------------------------------------------------
@app.on_event("startup")
def _startup_init_db():
    ensure_db_initialized_with_seed()

@app.on_event("startup")
async def _start_bg_tasks():
    app.state.scheduler_task = asyncio.create_task(scheduler.start_scheduler(poll_seconds=60))

@app.on_event("shutdown")
async def _stop_bg_tasks():
    task = getattr(app.state, "scheduler_task", None)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

# ---- Routers -----------------------------------------------------------------
app.include_router(auth_router)
app.include_router(stock_router)
app.include_router(restock_router)
app.include_router(bits_router)
app.include_router(usage_router)
app.include_router(shrouds_router)
app.include_router(equipment_router)
app.include_router(travel_router)
app.include_router(refuel_router)
app.include_router(handover_router)
app.include_router(audit_router)
app.include_router(jobs_router)
app.include_router(map_router)

# ---- Health / Debug ----------------------------------------------------------
@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})

@app.get("/debug/routes")
def debug_routes():
    routes: List[dict] = []
    for r in app.router.routes:
        name = getattr(r, "name", "")
        path = getattr(r, "path", "")
        routes.append({"path": path, "name": name})
    return routes

@app.get("/debug/settings")
def debug_settings(db=Depends(get_db)):
    s = db.scalar(select(Settings).limit(1))
    return {
        "status": "ok",
        "timezone": s.timezone if s else None,
        "reminder_horizon_days": s.reminder_horizon_days if s else None,
        "has_pin_hash": bool(s and s.crew_pin_hash),
    }

@app.get("/debug/db-check")
def debug_db_check(db=Depends(get_db)):
    try:
        _ = db.scalar(select(Settings).limit(1))
        _ = db.scalar(select(StockItem.id).limit(1))
        _ = db.scalar(select(Bit.id).limit(1))
        _ = db.scalar(select(EquipmentFault.id).limit(1))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": repr(e)}

# ---- Dashboard helpers -------------------------------------------------------
def _nodes_by_parent(nodes: list[LocationNode]) -> dict[int | None, list[LocationNode]]:
    buckets: dict[int | None, list[LocationNode]] = {}
    for n in nodes:
        buckets.setdefault(n.parent_id, []).append(n)
    for k in buckets:
        buckets[k].sort(key=lambda x: (x.name or "").lower())
    return buckets

def _render_tree_preview(nodes: list[LocationNode], max_nodes: int = 24) -> str:
    if not nodes:
        return "<p class='muted'>No locations yet.</p>"
    buckets = _nodes_by_parent(nodes)
    shown = 0
    def walk(pid: int | None) -> str:
        nonlocal shown
        children = buckets.get(pid, [])
        if not children:
            return ""
        items = []
        for n in children:
            if shown >= max_nodes:
                break
            shown += 1
            items.append(f"<li><span class='node-name'>{(n.name or '')}</span>{walk(n.id)}</li>")
        return f"<ul class='tree'>{''.join(items)}</ul>"
    html = walk(None)
    if shown >= max_nodes:
        html += "<p class='muted' style='margin:.25rem 0 0;'>…truncated — see full <a href='/map'>Locations</a></p>"
    return html

# ---- Root / Dashboard --------------------------------------------------------
from sqlalchemy import select as sa_select  # to avoid shadowing

@app.get("/", response_class=HTMLResponse)
def root(
    actor: str = Depends(current_actor),
    rig_id: str = Depends(current_rig_id),
    rig_title: str = Depends(current_rig_title),
    db=Depends(get_db),
):
    if not actor or not rig_id:
        return RedirectResponse("/auth/select", status_code=303)

    try:
        stocks = db.scalars(sa_select(StockItem)).all()
        low_crit = sum(1 for s in stocks if (s.on_rig_qty or 0) < (s.min_qty or 0) or (s.on_rig_qty or 0) < (s.buffer_qty or 0))
        open_restock = db.scalars(sa_select(RestockItem).where(RestockItem.is_closed == False)).all()  # noqa: E712
        bits_attention = db.scalars(sa_select(Bit).where(Bit.status.in_([BitStatus.NEEDS_RESHARPEN, BitStatus.VERY_USED]))).all()
        open_faults = db.scalars(sa_select(EquipmentFault).where(EquipmentFault.is_resolved == False)).all()  # noqa: E712
        open_handover_hi = db.scalars(sa_select(HandoverNote).where(HandoverNote.is_closed == False, HandoverNote.priority.in_([0,1]))).all()  # noqa: E712
        open_tasks_hi = db.scalars(sa_select(JobTask).where(JobTask.is_closed == False, JobTask.priority.in_([0,1]))).all()  # noqa: E712
        loc_nodes = db.scalars(sa_select(LocationNode)).all()
        locations_preview_html = _render_tree_preview(loc_nodes, max_nodes=24)

        critical_jobs_count = low_crit + len(open_restock) + len(open_faults) + len(bits_attention) + len(open_handover_hi) + len(open_tasks_hi)
    except OperationalError as e:
        msg = (
            "<html><head><link rel='stylesheet' href='/static/style.css'></head>"
            "<body class='container'>"
            "<h1>Database not initialized</h1>"
            "<p class='muted'>Tables are missing.</p>"
            "<form method='post' action='/admin/init'><button type='submit'>Initialize DB</button></form>"
            f"<pre style='margin-top:1rem;'>{str(e)}</pre>"
            "</body></html>"
        )
        return HTMLResponse(msg, status_code=500)

    attention_cards = [
        "<a class='card' href='/stock' style='border-left:6px solid #8b0000;'><strong>Low/Critical stock</strong><span class='muted'><br>"
        f"{low_crit} items</span></a>",
        "<a class='card' href='/restock' style='border-left:6px solid #1f6feb;'><strong>Restock orders</strong><span class='muted'><br>"
        f"{len(open_restock)} entries</span></a>",
        "<a class='card' href='/jobs' style='border-left:6px solid #f9844a;'><strong>Critical jobs</strong><span class='muted'><br>"
        f"{critical_jobs_count} items</span></a>",
        "<a class='card' href='/equipment' style='border-left:6px solid #e0a800;'><strong>Open equipment faults</strong><span class='muted'><br>"
        f"{len(open_faults)} open</span></a>",
    ]

    quick_cards = """
      <div class="cards">
        <a class="card" href="/stock/new"><strong>➕ Add stock item</strong><br><span class="muted">Name, unit, mins</span></a>
        <a class="card" href="/restock/new"><strong>➕ Add restock order</strong><br><span class="muted">Link to stock, priority</span></a>
        <a class="card" href="/jobs"><strong>➕ Add task</strong><br><span class="muted">Title, priority</span></a>
        <a class="card" href="/bits/new"><strong>➕ Add bit</strong><br><span class="muted">Serial, status, shroud</span></a>
        <a class="card" href="/equipment/new"><strong>➕ Add equipment</strong><br><span class="muted">Checks & faults</span></a>
        <a class="card" href="/handover/new"><strong>➕ Add handover note</strong><br><span class="muted">With priority</span></a>
        <a class="card" href="/travel/new"><strong>➕ Add travel log</strong><br><span class="muted">Location/time log</span></a>
        <a class="card" href="/refuel/new"><strong>➕ Add refuel log</strong><br><span class="muted">Before/after, time</span></a>
      </div>
    """

    sections_grid = f"""
      <div class="cards">
        <a class="card" href="/jobs"><strong>📋 Jobs</strong><br><span class="muted">Critical & tasks</span></a>
        <a class="card" href="/stock"><strong>📦 Stock</strong><br><span class="muted">On-rig, min/buffer, priorities</span></a>
        <a class="card" href="/restock"><strong>🛒 Restock</strong><br><span class="muted">Checklist & planning</span></a>
        <a class="card" href="/bits"><strong>🛠️ Bits & Shrouds</strong><br><span class="muted">Serials, status, usage</span></a>
        <a class="card" href="/usage"><strong>📊 Daily Usage</strong><br><span class="muted">Consumption logs</span></a>
        <a class="card" href="/equipment"><strong>⚙️ Equipment</strong><br><span class="muted">Checks & faults</span></a>
        <a class="card" href="/handover"><strong>🔄 Handover</strong><br><span class="muted">Notes & priorities</span></a>
        <a class="card" href="/travel"><strong>🚚 Travel</strong><br><span class="muted">Location/time log</span></a>
        <a class="card" href="/refuel"><strong>⛽ Refuel</strong><br><span class="muted">Before/after & reminders</span></a>
        <a class="card" href="/audit"><strong>📜 Audit</strong><br><span class="muted">Full change history</span></a>
        <a class="card" href="/offline"><strong>📴 Offline (PWA)</strong><br><span class="muted">Install / pre-cache</span></a>
      </div>
    """

    locations_block = f"""
      <h2 style="margin-top:2rem;">Locations tree</h2>
      {locations_preview_html}
      <p class="muted"><a href="/map">Open full Locations</a></p>
    """

    entries = recent_logs(db, limit=10)
    log_rows = []
    for e in entries:
        when = e.created_at.strftime("%Y-%m-%d %H:%M") if getattr(e, "created_at", None) else ""
        summary = (e.summary or "").replace("<", "&lt;")
        log_rows.append(
            f"<tr><td>#{e.id}</td><td>{when}</td><td>{e.actor}</td>"
            f"<td>{e.entity}[{e.entity_id}] {e.action}</td><td><small>{summary}</small></td></tr>"
        )
    recent_html = (
        "<p class='muted'>No recent activity.</p>"
        if not log_rows
        else "<table><thead><tr><th>ID</th><th>When</th><th>Actor</th><th>What</th><th>Summary</th></tr></thead>"
             f"<tbody>{''.join(log_rows)}</tbody></table>"
    )

    title = (rig_title or rig_id or "Rig") + " Dashboard - Offsider tools v0.1"
    html = f"""
    <html>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="stylesheet" href="/static/style.css">
        <title>{title}</title>
      </head>
      <body class="container">
        <h1>{title}</h1>
        <p class="muted">Signed in as <strong>{actor}</strong>.
          <form class="noprint" method="post" action="/auth/logout" style="display:inline;">
            <button class="btn" type="submit">Sign out</button>
          </form>
        </p>

        <h2>Attention</h2>
        <div class="cards">
          {''.join(attention_cards)}
        </div>

        <h2>Quick links</h2>
        {quick_cards}

        <h2>Sections</h2>
        {sections_grid}

        {locations_block}

        <h2 style="margin-top:2rem;">Recent Activity</h2>
        {recent_html}

        <p style="margin-top:1rem;"><a href="/docs" class="muted">API docs</a></p>
      </body>
    </html>
    """
    return HTMLResponse(html)
