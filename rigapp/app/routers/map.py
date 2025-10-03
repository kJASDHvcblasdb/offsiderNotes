from __future__ import annotations

from html import escape
from typing import Dict, List

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import LocationNode, StockLocationLink
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/map", tags=["map"])

# ---- helpers -----------------------------------------------------------------

def _build_tree(all_nodes: List[LocationNode]) -> Dict[int | None, List[LocationNode]]:
    """Group nodes by parent_id for simple recursive rendering."""
    buckets: Dict[int | None, List[LocationNode]] = {}
    for n in all_nodes:
        buckets.setdefault(n.parent_id, []).append(n)
    # Sort siblings by name
    for k in buckets:
        buckets[k].sort(key=lambda x: (x.name or "").lower())
    return buckets

def _render_subtree(parent_id: int | None, buckets, counts) -> str:
    children = buckets.get(parent_id, [])
    if not children:
        return ""
    items = []
    for n in children:
        c = counts.get(n.id, 0)
        badge = f"<span class='chip chip-low' title='Linked stock items'>{c}</span>" if c else ""
        items.append(
            f"<li>"
            f"<a class='node-name' href='/map/{n.id}'>{escape(n.name)}</a> "
            f"{badge} "
            f"<span class='muted small'>{escape(n.kind or '')}</span>"
            f"<div class='actions' style='margin:.25rem 0;'>"
            f"<a class='btn btn-sm' href='/map/{n.id}/edit'>Edit</a>"
            f"<a class='btn btn-sm' href='/map/{n.id}/move'>Move</a>"
            f"<form method='post' action='/map/{n.id}/delete' style='display:inline'>"
            f"<button class='btn btn-sm' type='submit' onclick='return confirm(\"Delete {n.name}?\\n(Children will be orphaned to root; links preserved.)\")'>Delete</button>"
            f"</form>"
            f"</div>"
            f"{_render_subtree(n.id, buckets, counts)}"
            f"</li>"
        )
    return f"<ul class='tree'>{''.join(items)}</ul>"

# ---- index -------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def map_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
    q: str = Query("", description="Filter by name"),
):
    # Fetch all nodes + counts of linked stock per node
    nodes = db.scalars(select(LocationNode)).all()
    if q:
        ql = (q or "").strip().lower()
        nodes = [n for n in nodes if (n.name and ql in n.name.lower())]

    counts_q = (
        select(StockLocationLink.location_node_id, func.count(StockLocationLink.id))
        .group_by(StockLocationLink.location_node_id)
    )
    counts = {node_id: cnt for node_id, cnt in db.execute(counts_q).all()}

    buckets = _build_tree(nodes)

    # Controls (labels & migrate links removed)
    controls = f"""
      <form method="get" action="/map" class="form" style="display:flex; gap:.75rem; align-items:flex-end; flex-wrap:wrap;">
        <div style="min-width:260px;">
          <label>Search</label>
          <input name="q" value="{escape(q or '')}" placeholder="name contains…">
        </div>
        <div class="actions">
          <button class="btn" type="submit">Filter</button>
          <a class="btn" href="/map">Reset</a>
          <a class="btn" href="/map/new">➕ Add node</a>
        </div>
      </form>
    """

    body = controls + _render_subtree(None, buckets, counts)
    if not nodes:
        body += "<p class='muted'>No locations yet. Add your first node.</p>"

    return wrap_page(title="Map / Locations", body_html=body, actor=actor, rig_title=rig)

# ---- detail ------------------------------------------------------------------

@router.get("/{node_id}", response_class=HTMLResponse)
def map_detail(
    node_id: int,
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    n = db.get(LocationNode, node_id)
    if not n:
        return RedirectResponse("/map", status_code=303)

    # breadcrumb
    crumb = []
    cur = n
    seen: set[int] = set()
    while cur and cur.id not in seen:
        seen.add(cur.id)
        crumb.append(cur.name or "")
        cur = db.get(LocationNode, cur.parent_id) if cur.parent_id else None
    crumb_html = " / ".join(escape(x) for x in reversed(crumb))

    body = f"""
      <p class="muted">Breadcrumb: {crumb_html or '(root)'}</p>
      <table>
        <tr><th style="width:140px;">Name</th><td>{escape(n.name)}</td></tr>
        <tr><th>Kind</th><td>{escape(n.kind or '')}</td></tr>
        <tr><th>Notes</th><td>{escape(n.notes or '')}</td></tr>
      </table>
      <div class="actions">
        <a class="btn" href="/map/{n.id}/edit">Edit</a>
        <a class="btn" href="/map/{n.id}/move">Move</a>
        <form method="post" action="/map/{n.id}/delete" style="display:inline">
          <button class="btn" type="submit" onclick="return confirm('Delete {escape(n.name)}?')">Delete</button>
        </form>
        <a class="btn" href="/map">⬅ Back</a>
      </div>
    """
    return wrap_page(title=f"Location: {n.name}", body_html=body, actor=actor, rig_title=rig)

# ---- new ---------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
def map_new_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
    parent_id: int | None = Query(None),
):
    options = ["<option value=''>— root —</option>"]
    for n in db.scalars(select(LocationNode).order_by(LocationNode.name)).all():
        sel = " selected" if parent_id and n.id == parent_id else ""
        options.append(f"<option value='{n.id}'{sel}>{escape(n.name)}</option>")

    body = f"""
      <form method="post" action="/map/new" class="form">
        <label>Name <input name="name" required></label>
        <label>Kind <input name="kind" placeholder="CONTAINER / BAY / SHELF"></label>
        <label>Parent
          <select name="parent_id">
            {''.join(options)}
          </select>
        </label>
        <label>Notes <textarea name="notes" rows="3"></textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/map">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title="New Location Node", body_html=body, actor=actor, rig_title=rig)

@router.post("/new")
def map_new(
    actor: str = Depends(current_actor),
    name: str = Form(...),
    kind: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    db=Depends(get_db),
):
    pid = int(parent_id) if parent_id else None
    n = LocationNode(name=name, kind=(kind or None), parent_id=pid, notes=(notes or None))
    db.add(n)
    db.commit()
    write_log(db, actor=actor or "crew", entity="location", entity_id=n.id, action="create", summary=name)
    return RedirectResponse("/map", status_code=303)

# ---- edit --------------------------------------------------------------------

@router.get("/{node_id}/edit", response_class=HTMLResponse)
def map_edit_form(
    node_id: int,
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    n = db.get(LocationNode, node_id)
    if not n:
        return RedirectResponse("/map", status_code=303)

    options = ["<option value=''>— root —</option>"]
    for other in db.scalars(select(LocationNode).order_by(LocationNode.name)).all():
        if other.id == n.id:
            continue
        sel = " selected" if other.id == (n.parent_id or 0) else ""
        options.append(f"<option value='{other.id}'{sel}>{escape(other.name)}</option>")

    body = f"""
      <form method="post" action="/map/{n.id}/edit" class="form">
        <label>Name <input name="name" value="{escape(n.name)}" required></label>
        <label>Kind <input name="kind" value="{escape(n.kind or '')}"></label>
        <label>Parent
          <select name="parent_id">
            {''.join(options)}
          </select>
        </label>
        <label>Notes <textarea name="notes" rows="3">{escape(n.notes or '')}</textarea></label>
        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/map">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title=f"Edit: {n.name}", body_html=body, actor=actor, rig_title=rig)

@router.post("/{node_id}/edit")
def map_edit(
    node_id: int,
    actor: str = Depends(current_actor),
    name: str = Form(...),
    kind: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    db=Depends(get_db),
):
    n = db.get(LocationNode, node_id)
    if not n:
        return RedirectResponse("/map", status_code=303)
    before = n.name
    n.name = name
    n.kind = kind or None
    n.parent_id = int(parent_id) if parent_id else None
    n.notes = notes or None
    db.commit()
    write_log(db, actor=actor or "crew", entity="location", entity_id=n.id, action="update", summary=f"{before} → {n.name}")
    return RedirectResponse("/map", status_code=303)

# ---- move (UI nicety, identical to edit parent) ------------------------------

@router.get("/{node_id}/move", response_class=HTMLResponse)
def map_move_form(
    node_id: int,
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    n = db.get(LocationNode, node_id)
    if not n:
        return RedirectResponse("/map", status_code=303)

    options = ["<option value=''>— root —</option>"]
    for other in db.scalars(select(LocationNode).order_by(LocationNode.name)).all():
        if other.id == n.id:
            continue
        sel = " selected" if other.id == (n.parent_id or 0) else ""
        options.append(f"<option value='{other.id}'{sel}>{escape(other.name)}</option>")

    body = f"""
      <form method="post" action="/map/{n.id}/move" class="form">
        <p>Move <strong>{escape(n.name)}</strong> to:</p>
        <label>Parent
          <select name="parent_id">
            {''.join(options)}
          </select>
        </label>
        <div class="actions">
          <button class="btn" type="submit">Move</button>
          <a class="btn" href="/map">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title=f"Move: {n.name}", body_html=body, actor=actor, rig_title=rig)

@router.post("/{node_id}/move")
def map_move(
    node_id: int,
    actor: str = Depends(current_actor),
    parent_id: str = Form(""),
    db=Depends(get_db),
):
    n = db.get(LocationNode, node_id)
    if n:
        n.parent_id = int(parent_id) if parent_id else None
        db.commit()
        write_log(db, actor=actor or "crew", entity="location", entity_id=n.id, action="move", summary=f"Moved to parent {n.parent_id}")
    return RedirectResponse("/map", status_code=303)

# ---- delete ------------------------------------------------------------------

@router.post("/{node_id}/delete")
def map_delete(
    node_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    n = db.get(LocationNode, node_id)
    if not n:
        return RedirectResponse("/map", status_code=303)

    # Orphan children to root
    for ch in db.scalars(select(LocationNode).where(LocationNode.parent_id == n.id)).all():
        ch.parent_id = None

    # Keep stock links intact (still point to this node). If you wanted to
    # reassign them to root (or another), you could do it here.

    db.delete(n)
    db.commit()
    write_log(db, actor=actor or "crew", entity="location", entity_id=node_id, action="delete", summary=n.name or "")
    return RedirectResponse("/map", status_code=303)

# ---------- quick add (embedded in Stock forms) -------------------------------

@router.post("/quick-new")
def quick_new_location(
    actor: str = Depends(current_actor),
    name: str = Form(...),
    parent_id: str = Form(""),
    return_to: str = Form("/stock/new"),
    db=Depends(get_db),
):
    # Create the node
    pid = int(parent_id) if parent_id else None
    node = LocationNode(name=name.strip(), parent_id=pid)
    db.add(node)
    db.commit()
    write_log(db, actor=actor or "crew", entity="location", entity_id=node.id, action="create", summary=f"{node.name}")

    # Guard: only app-internal paths
    rt = return_to or "/stock/new"
    if not rt.startswith("/"):
        rt = "/stock/new"

    # Append selected_node_id so the Stock form preselects the new node
    joiner = "&" if "?" in rt else "?"
    return RedirectResponse(f"{rt}{joiner}selected_node_id={node.id}", status_code=303)
