from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import quote_plus, urlencode
from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, or_, and_

from ..db import get_db
from ..auth import require_reader, current_actor, current_rig_title
from ..models import StockItem, LocationNode, StockLocationLink
from ..audit import write_log
from ..ui import wrap_page

router = APIRouter(prefix="/stock", tags=["stock"])


def _laydown_predicate():
    return or_(
        StockItem.location.ilike("%container%"),
        StockItem.location.ilike("%sea container%"),
        StockItem.location.ilike("%laydown%"),
    )


def _render_location_breadcrumb(location: str | None) -> str:
    if not location:
        return ""
    parts = [p.strip() for p in (location or "").split("/") if p.strip()]
    if not parts:
        return escape(location or "")
    links = []
    for i in range(len(parts)):
        prefix = " / ".join(parts[: i + 1])
        q = quote_plus(prefix)
        label = escape(parts[i])
        links.append(f"<a class='muted' href='/stock?q={q}'>{label}</a>")
    sep = " <span class='muted'>›</span> "
    return sep.join(links)


def _nodes_by_parent(nodes: list[LocationNode]) -> dict[int | None, list[LocationNode]]:
    buckets: dict[int | None, list[LocationNode]] = {}
    for n in nodes:
        buckets.setdefault(n.parent_id, []).append(n)
    for k in buckets:
        buckets[k].sort(key=lambda x: (x.name or "").lower())
    return buckets


def _breadcrumb_for_node(db, node_id: int | None) -> list[str]:
    if not node_id:
        return []
    parts: list[str] = []
    seen: set[int] = set()
    cur = db.get(LocationNode, node_id)
    while cur and cur.id not in seen:
        seen.add(cur.id)
        parts.append(cur.name or "")
        cur = db.get(LocationNode, cur.parent_id) if cur.parent_id else None
    parts.reverse()
    return parts


def _breadcrumb_text_for_node(db, node_id: int | None) -> str:
    return " / ".join([p for p in _breadcrumb_for_node(db, node_id) if p])


def _render_linked_breadcrumb(db, node_id: int | None) -> str:
    parts = _breadcrumb_for_node(db, node_id)
    if not parts:
        return ""
    links = []
    for i in range(len(parts)):
        prefix = " / ".join(parts[: i + 1])
        q = quote_plus(prefix)
        label = escape(parts[i])
        links.append(f"<a class='muted' href='/stock?q={q}'>{label}</a>")
    sep = " <span class='muted'>›</span> "
    return sep.join(links)


def _location_select_options(db, selected_id: int | None = None) -> str:
    nodes = db.scalars(select(LocationNode)).all()
    buckets = _nodes_by_parent(nodes)

    def walk(parent_id: int | None, depth: int) -> list[str]:
        out: list[str] = []
        for n in buckets.get(parent_id, []):
            indent = " " * (depth * 2)
            sel = " selected" if selected_id and n.id == selected_id else ""
            label = f"{indent}{escape(n.name)}"
            out.append(f"<option value='{n.id}'{sel}>{label}</option>")
            out.extend(walk(n.id, depth + 1))
        return out

    opts = ["<option value=''>— none —</option>"]
    opts.extend(walk(None, 0))
    return "\n".join(opts)


def _iso(dt: datetime | None) -> str:
    return (dt or datetime.utcfromtimestamp(0)).replace(microsecond=0).isoformat() + "Z"


def _parse_client_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        s = ts.rstrip("Z")
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ---- index -------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def stock_index(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
    q: str = Query("", description="Search query over name, unit, location (free-text or linked breadcrumb)"),
    area: str = Query("all", description="Filter area: all | rig | laydown"),
):
    stmt = select(StockItem)

    if area.lower() == "laydown":
        stmt = stmt.where(_laydown_predicate())
    elif area.lower() == "rig":
        stmt = stmt.where(
            or_(
                StockItem.location.is_(None),
                and_(StockItem.location.is_not(None), ~_laydown_predicate()),
            )
        )

    stmt = stmt.order_by(StockItem.name)
    all_items = db.scalars(stmt).all()

    items: list[StockItem] = all_items
    if q:
        ql = (q or "").strip().lower()
        links = db.scalars(
            select(StockLocationLink).where(
                StockLocationLink.stock_item_id.in_([s.id for s in all_items] or [0])
            )
        ).all()
        link_by_stock_id: dict[int, StockLocationLink] = {lnk.stock_item_id: lnk for lnk in links}

        def matches(s: StockItem) -> bool:
            if (s.name or "").lower().find(ql) != -1:
                return True
            if (s.unit or "").lower().find(ql) != -1:
                return True
            if (s.location or "").lower().find(ql) != -1:
                return True
            link = link_by_stock_id.get(s.id)
            if link:
                crumb = _breadcrumb_text_for_node(db, link.location_node_id).lower()
                if crumb.find(ql) != -1:
                    return True
            return False

        items = [s for s in all_items if matches(s)]

    def _severity_rank(s: StockItem) -> int:
        qty = s.on_rig_qty or 0
        min_q = s.min_qty or 0
        buf_q = s.buffer_qty or 0
        if qty < min_q:
            return 0
        if qty < buf_q:
            return 1
        return 2

    items.sort(key=lambda s: (_severity_rank(s), (s.name or "").lower()))

    q_val = escape(q or "")
    area_val = (area or "all").lower()
    select_area = f"""
      <form method="get" action="/stock" class="form"
            style="margin:.25rem 0 1rem; display:flex; gap:.9rem; align-items:flex-end; flex-wrap:wrap;">
        <div style="display:flex; flex-direction:column; min-width:260px; padding:.25rem .5rem .5rem 0;">
          <label>Search</label>
          <input name="q" value="{q_val}" placeholder="name, unit, location, or breadcrumb">
        </div>
        <div style="display:flex; flex-direction:column; min-width:220px; padding:.25rem .5rem .5rem 0;">
          <label>Area</label>
          <select name="area">
            <option value="all" {"selected" if area_val=="all" else ""}>All</option>
            <option value="rig" {"selected" if area_val=="rig" else ""}>On-rig</option>
            <option value="laydown" {"selected" if area_val=="laydown" else ""}>Laydown (containers)</option>
          </select>
        </div>
        <div class="actions" style="padding:.25rem .5rem .5rem 0;">
          <button class="btn" type="submit">Filter</button>
          <a class="btn" href="/stock">Reset</a>
          <a class="btn" href="/stock/new">➕ Add stock item</a>
        </div>
      </form>
    """

    q_url = quote_plus(q or "")
    area_url = quote_plus(area or "all")

    links = db.scalars(
        select(StockLocationLink).where(StockLocationLink.stock_item_id.in_([s.id for s in items] or [0]))
    ).all()
    link_by_stock_id: dict[int, StockLocationLink] = {lnk.stock_item_id: lnk for lnk in links}

    rows = []
    for s in items:
        qty = s.on_rig_qty or 0
        min_q = s.min_qty or 0
        buf_q = s.buffer_qty or 0

        is_critical = qty < min_q
        is_low = (not is_critical) and (qty < buf_q)

        badge = ""
        tr_class = ""
        if is_critical:
            badge = " <span class='badge badge-critical'>CRITICAL</span>"
            tr_class = " class='row-critical'"
        elif is_low:
            badge = " <span class='badge badge-attention'>LOW</span>"
            tr_class = " class='row-attention'"

        adjust_action_base = f"/stock/{s.id}/adjust?q={q_url}&area={area_url}"
        # Include optimistic-concurrency token (updated_at) for +/- forms
        last_ts = _iso(s.updated_at)
        adjust_html = f"""
          <div class="btn-group btn-group-inline">
            <form method="post" action="{adjust_action_base}" style="display:inline">
              <input type="hidden" name="delta" value="-5">
              <input type="hidden" name="if_unmodified_since" value="{last_ts}">
              <button class="btn btn-sm" type="submit">−5</button>
            </form>
            <form method="post" action="{adjust_action_base}" style="display:inline">
              <input type="hidden" name="delta" value="-1">
              <input type="hidden" name="if_unmodified_since" value="{last_ts}">
              <button class="btn btn-sm" type="submit">−1</button>
            </form>
            <form method="post" action="{adjust_action_base}" style="display:inline">
              <input type="hidden" name="delta" value="1">
              <input type="hidden" name="if_unmodified_since" value="{last_ts}">
              <button class="btn btn-sm" type="submit">+1</button>
            </form>
            <form method="post" action="{adjust_action_base}" style="display:inline">
              <input type="hidden" name="delta" value="5">
              <input type="hidden" name="if_unmodified_since" value="{last_ts}">
              <button class="btn btn-sm" type="submit">+5</button>
            </form>
          </div>
        """

        restock_btn = ""
        if is_low or is_critical:
            target = (s.min_qty or 0) + (s.buffer_qty or 0)
            need = max(target - qty, 1)
            restock_url = f"/restock/new?stock_item_id={s.id}&qty={need}&unit={quote_plus(s.unit or 'ea')}"
            restock_btn = f"<a class='btn btn-sm' href='{restock_url}'>Restock +{need}</a> "

        actions_html = (
            f"{adjust_html}"
            f"{restock_btn}"
            f"<a class='btn' href='/stock/{s.id}/edit'>Edit</a> "
            f"<form method='post' action='/stock/{s.id}/delete' style='display:inline'>"
            f"<button class='btn' type='submit' onclick='return confirm(\"Delete {escape(s.name)}?\")'>Delete</button>"
            f"</form>"
        )

        linked = link_by_stock_id.get(s.id)
        if linked:
            location_html = _render_linked_breadcrumb(db, linked.location_node_id)
            if not location_html:
                location_html = "<span class='muted'>[missing location]</span>"
        else:
            location_html = _render_location_breadcrumb(s.location)

        rows.append(
            f"<tr{tr_class}>"
            f"<td>{escape(s.name)}{badge}</td>"
            f"<td>{s.on_rig_qty}</td><td>{s.min_qty}</td><td>{s.buffer_qty}</td>"
            f"<td>{escape(s.unit or '')}</td><td>{location_html}</td>"
            f"<td>{actions_html}</td>"
            "</tr>"
        )

    table = (
        "<p class='muted'>No stock items match your filters.</p>"
        if not rows
        else (
            "<table><thead><tr>"
            "<th>Name</th><th>QTY</th><th>Min</th><th>Buffer</th><th>Unit</th><th>Location</th><th></th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    )

    body = select_area + table
    return wrap_page(title="Stock", body_html=body, actor=actor, rig_title=rig)


# ---- new ---------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
def stock_new_form(
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
    selected_node_id: int | None = Query(None),
):
    location_options = _location_select_options(db, selected_id=selected_node_id)

    body = f"""
      <form method="post" action="/stock/new" class="form">
        <label>Name <input name="name" required></label>
        <label>QTY <input type="number" name="on_rig_qty" value="0"></label>
        <label>Min qty <input type="number" name="min_qty" value="0"></label>
        <label>Buffer qty <input type="number" name="buffer_qty" value="0"></label>
        <label>Unit <input name="unit" value="ea"></label>

        <h3>Location</h3>
        <label>Choose existing
          <select name="location_node_id">
            {location_options}
          </select>
        </label>

        <details class="muted" style="margin:.4rem 0;">
          <summary>Or use free text (legacy)</summary>
          <label>Location (text)
            <input name="location" placeholder="e.g. Sea Container A/Bay 1/Shelf 1">
            <small class="muted">Tip: use slashes to create breadcrumbs (e.g., A/Bay 1/Shelf 1)</small>
          </label>
        </details>

        <div class="actions">
          <button class="btn" type="submit">Save</button>
          <a class="btn" href="/stock">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title="New Stock Item", body_html=body, actor=actor, rig_title=rig)


@router.post("/new")
def stock_new(
    actor: str = Depends(current_actor),
    name: str = Form(...),
    on_rig_qty: int = Form(0),
    min_qty: int = Form(0),
    buffer_qty: int = Form(0),
    unit: str = Form("ea"),
    location: str = Form(""),
    location_node_id: str = Form(""),
    db=Depends(get_db),
):
    s = StockItem(
        name=name,
        on_rig_qty=on_rig_qty,
        min_qty=min_qty,
        buffer_qty=buffer_qty,
        unit=unit,
        location=(location or None),
    )
    db.add(s)
    db.commit()

    if location_node_id:
        try:
            node_id_int = int(location_node_id)
        except ValueError:
            node_id_int = None
        if node_id_int:
            existing = db.scalar(select(StockLocationLink).where(StockLocationLink.stock_item_id == s.id))
            if existing:
                existing.location_node_id = node_id_int
            else:
                db.add(StockLocationLink(stock_item_id=s.id, location_node_id=node_id_int))
            db.commit()

    write_log(db, actor=actor or "crew", entity="stock", entity_id=s.id, action="create", summary=name)
    return RedirectResponse("/stock", status_code=303)


# --------- Quick adjust (+/-) (with optimistic concurrency) -------------------

@router.post("/{stock_id}/adjust")
def stock_adjust(
    stock_id: int,
    delta: int = Form(...),
    if_unmodified_since: str = Form(""),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
    q: str = Query(""),
    area: str = Query("all"),
):
    s = db.get(StockItem, stock_id)
    if not s:
        return RedirectResponse("/stock", status_code=303)

    client_ts = _parse_client_ts(if_unmodified_since)
    current_ts = s.updated_at

    # If we have a client token and server is newer -> conflict
    if client_ts and current_ts and current_ts > client_ts:
        body = f"""
          <p class="danger"><strong>Update blocked:</strong> This item was changed by someone else after you loaded the page.</p>
          <p class="muted">Item: <strong>{escape(s.name)}</strong> — current quantity is <strong>{s.on_rig_qty or 0}</strong>.</p>
          <div class="actions">
            <a class="btn" href="/stock">Back to Stock</a>
          </div>
        """
        return wrap_page(title="Conflict — Stock adjust", body_html=body)

    before = s.on_rig_qty or 0
    after = before + int(delta)
    if after < 0:
        after = 0

    s.on_rig_qty = after
    db.commit()
    write_log(
        db,
        actor=actor or "crew",
        entity="stock",
        entity_id=s.id,
        action="adjust",
        summary=f"{s.name}: {before} → {after} ({'+' if delta>=0 else ''}{delta})",
    )

    params = urlencode({"q": q or "", "area": area or "all"})
    return RedirectResponse(f"/stock?{params}", status_code=303)


# --------- Edit flow (with optimistic concurrency) ----------------------------

@router.get("/{stock_id}/edit", response_class=HTMLResponse)
def stock_edit_form(
    stock_id: int,
    ok: bool = Depends(require_reader),
    rig: str = Depends(current_rig_title),
    actor: str = Depends(current_actor),
    db=Depends(get_db),
    selected_node_id: int | None = Query(None),
):
    s = db.get(StockItem, stock_id)
    if not s:
        return RedirectResponse("/stock", status_code=303)

    link = db.scalar(select(StockLocationLink).where(StockLocationLink.stock_item_id == s.id))
    current_selected = selected_node_id if selected_node_id is not None else (link.location_node_id if link else None)
    location_options = _location_select_options(db, selected_id=current_selected)
    token = _iso(s.updated_at)

    body = f"""
      <form method="post" action="/stock/{s.id}/edit" class="form">
        <input type="hidden" name="if_unmodified_since" value="{token}">
        <label>Name <input name="name" required value="{escape(s.name)}"></label>
        <label>QTY <input type="number" name="on_rig_qty" value="{s.on_rig_qty or 0}"></label>
        <label>Min qty <input type="number" name="min_qty" value="{s.min_qty or 0}"></label>
        <label>Buffer qty <input type="number" name="buffer_qty" value="{s.buffer_qty or 0}"></label>
        <label>Unit <input name="unit" value="{escape(s.unit or 'ea')}"></label>

        <h3>Location</h3>
        <label>Choose existing
          <select name="location_node_id">
            {location_options}
          </select>
        </label>

        <details class="muted" style="margin:.4rem 0;">
          <summary>Or use free text (legacy)</summary>
          <label>Location (text)
            <input name="location" value="{escape(s.location or '')}">
            <small class="muted">Tip: use slashes to create breadcrumbs (e.g., A/Bay 1/Shelf 1)</small>
          </label>
        </details>

        <div class="actions">
          <button class="btn" type="submit">Save changes</button>
          <a class="btn" href="/stock">Cancel</a>
        </div>
      </form>
    """
    return wrap_page(title=f"Edit: {s.name}", body_html=body, actor=actor, rig_title=rig)


@router.post("/{stock_id}/edit")
def stock_edit(
    stock_id: int,
    actor: str = Depends(current_actor),
    if_unmodified_since: str = Form(""),
    name: str = Form(...),
    on_rig_qty: int = Form(0),
    min_qty: int = Form(0),
    buffer_qty: int = Form(0),
    unit: str = Form("ea"),
    location: str = Form(""),
    location_node_id: str = Form(""),
    db=Depends(get_db),
):
    s = db.get(StockItem, stock_id)
    if not s:
        return RedirectResponse("/stock", status_code=303)

    client_ts = _parse_client_ts(if_unmodified_since)
    if client_ts and s.updated_at and s.updated_at > client_ts:
        body = f"""
          <p class="danger"><strong>Save blocked:</strong> This item was changed by someone else after you opened the form.</p>
          <p class="muted">Item: <strong>{escape(s.name)}</strong> — current quantity is <strong>{s.on_rig_qty or 0}</strong>.</p>
          <div class="actions">
            <a class="btn" href="/stock/{s.id}/edit">Reload form</a>
            <a class="btn" href="/stock">Back to Stock</a>
          </div>
        """
        return wrap_page(title="Conflict — Stock edit", body_html=body)

    before = f"{s.name} [{s.on_rig_qty}/{s.min_qty}/{s.buffer_qty} {s.unit}]"
    s.name = name
    s.on_rig_qty = on_rig_qty
    s.min_qty = min_qty
    s.buffer_qty = buffer_qty
    s.unit = unit
    s.location = (location or None)
    db.commit()

    link = db.scalar(select(StockLocationLink).where(StockLocationLink.stock_item_id == s.id))
    node_id_int: int | None = None
    if location_node_id:
        try:
            node_id_int = int(location_node_id)
        except ValueError:
            node_id_int = None

    if node_id_int:
        if link:
            link.location_node_id = node_id_int
        else:
            db.add(StockLocationLink(stock_item_id=s.id, location_node_id=node_id_int))
        db.commit()
    else:
        if link:
            db.delete(link)
            db.commit()

    after = f"{s.name} [{s.on_rig_qty}/{s.min_qty}/{s.buffer_qty} {s.unit}]"
    write_log(
        db,
        actor=actor or "crew",
        entity="stock",
        entity_id=s.id,
        action="update",
        summary=f"{before} → {after}",
    )
    return RedirectResponse("/stock", status_code=303)


@router.post("/{stock_id}/delete")
def stock_delete(
    stock_id: int,
    actor: str = Depends(current_actor),
    db=Depends(get_db),
):
    s = db.get(StockItem, stock_id)
    if s:
        name = s.name
        link = db.scalar(select(StockLocationLink).where(StockLocationLink.stock_item_id == s.id))
        if link:
            db.delete(link)
        db.delete(s)
        db.commit()
        write_log(db, actor=actor or "crew", entity="stock", entity_id=stock_id, action="delete", summary=name or "")
    return RedirectResponse("/stock", status_code=303)
