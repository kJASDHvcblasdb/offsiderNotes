# rigapp/app/routers/rigs.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db import (
    list_known_rigs,
    rig_exists,
    add_rig,
    select_rig,
    get_rig_info,
    get_default_rig_id,
)

router = APIRouter()


def _page(title: str, body_html: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header class="topbar">
    <div class="brand"><a href="/">Offsider Notes</a></div>
    <nav class="nav">
      <a href="/rigs">Rigs</a>
    </nav>
  </header>
  <main class="container">
    <h1>{title}</h1>
    {body_html}
  </main>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/rigs", response_class=HTMLResponse)
def list_rigs() -> HTMLResponse:
    rigs = list_known_rigs()
    current = get_default_rig_id()
    rows = []
    if not rigs:
        rows.append("<p class='muted'>No rigs yet.</p>")
    else:
        rows.append("<ul class='card-list'>")
        for r in rigs:
            rid = r["id"]
            info = get_rig_info(rid)
            title = info.get("title") or rid
            subtitle = info.get("subtitle") or ""
            current_badge = " <span class='badge'>selected</span>" if rid == current else ""
            rows.append(
                f"""<li class="card">
  <div class="card-body">
    <div class="card-title">{rid} — {title}{current_badge}</div>
    <div class="card-subtitle">{subtitle}</div>
    <div class="card-actions">
      <a class="btn" href="/r/{rid}/">Open</a>
      <form method="post" action="/rigs/select" style="display:inline">
        <input type="hidden" name="rig_id" value="{rid}">
        <button class="btn" type="submit">Select</button>
      </form>
    </div>
  </div>
</li>"""
            )
        rows.append("</ul>")

    rows.append(
        """<div class="mt">
  <a class="btn primary" href="/rigs/new">Add a rig</a>
</div>"""
    )
    return _page("Rigs", "\n".join(rows))


@router.post("/rigs/select")
async def select_rig_post(request: Request):
    form = dict(await request.form())  # type: ignore[assignment]
    rid = (form.get("rig_id") or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="rig_id is required")
    if not rig_exists(rid):
        raise HTTPException(status_code=404, detail=f"Rig '{rid}' not found.")
    select_rig(rid)
    return RedirectResponse(url=f"/r/{rid}/", status_code=303)


@router.get("/rigs/new", response_class=HTMLResponse)
def new_rig_form() -> HTMLResponse:
    body = """
<form class="card" method="post" action="/rigs/new">
  <div class="card-body">
    <div class="form-row">
      <label for="rig_id">Rig ID</label>
      <input id="rig_id" name="rig_id" placeholder="e.g. RC3007" required>
    </div>
    <div class="form-row">
      <label for="title">Title (display name)</label>
      <input id="title" name="title" placeholder="RC3007">
    </div>
    <div class="form-row">
      <label for="subtitle">Subtitle (optional)</label>
      <input id="subtitle" name="subtitle" placeholder="Fleet ops — Perth">
    </div>
    <div class="form-row">
      <button class="btn primary" type="submit">Create</button>
      <a class="btn" href="/rigs">Cancel</a>
    </div>
  </div>
</form>
    """
    return _page("Add a rig", body)


@router.post("/rigs/new")
async def create_rig(request: Request):
    form = dict(await request.form())  # type: ignore[assignment]
    rid = (form.get("rig_id") or "").strip()
    title = (form.get("title") or "").strip()
    subtitle = (form.get("subtitle") or "").strip()

    if not rid:
        raise HTTPException(status_code=400, detail="Rig ID is required")

    if rig_exists(rid):
        # Already present -> just select it and go
        select_rig(rid)
        return RedirectResponse(url=f"/r/{rid}/", status_code=303)

    # Add, ensure dir and DB, then select
    add_rig(rid, title=title or rid, subtitle=subtitle or None)
    select_rig(rid)
    return RedirectResponse(url=f"/r/{rid}/", status_code=303)
