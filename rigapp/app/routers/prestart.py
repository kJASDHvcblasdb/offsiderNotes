from __future__ import annotations
from ..ui import page_auto
import csv
import io
import datetime as dt

from ..ui import page_auto
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Prestart, Shift
from ..auth import require_writer, current_actor
from ..audit import write_log, snapshot

router = APIRouter(prefix="/prestart", tags=["prestart"])


@router.get("", response_class=HTMLResponse)
def list_prestarts(db: Session = Depends(get_db)):
    rows = []
    items = db.scalars(select(Prestart).order_by(desc(Prestart.date), desc(Prestart.shift))).all()
    for p in items:
        rows.append(
            f"<tr>"
            f"<td>{p.date}</td>"
            f"<td>{p.shift.value}</td>"
            f"<td><small>{(p.notes or '').replace('<','&lt;')}</small></td>"
            f"<td>{p.created_by or ''}</td>"
            f"<td><a href='/prestart/{p.id}/edit'>Edit</a></td>"
            f"</tr>"
        )
    html = f"""
    <html><body style="font-family: system-ui; max-width: 1000px; margin: 2rem auto;">
      <h2>Prestart</h2>
      <div style="margin-bottom:1rem;">
        <a href="/prestart/new">+ New</a> · <a href="/prestart/export.csv">Export CSV</a>
      </div>
      <table border="1" cellpadding="6" cellspacing="0" width="100%">
        <thead><tr><th>Date</th><th>Shift</th><th>Notes</th><th>Created by</th><th>Actions</th></tr></thead>
        <tbody>{''.join(rows) or "<tr><td colspan='5'>No prestarts yet.</td></tr>"}</tbody>
      </table>
      <p style="margin-top:1rem;"><a href="/">Back</a></p>
    </body></html>
    """
    return page_auto(html)


@router.get("/new", response_class=HTMLResponse)
def new_prestart_form(ok: bool = Depends(require_writer)):
    today = dt.date.today().isoformat()
    html = f"""
    <html><body style="font-family: system-ui; max-width: 640px; margin: 2rem auto;">
      <h2>New Prestart</h2>
      <form method="post" action="/prestart/new">
        <label>Date<br><input name="date" type="date" value="{today}" required></label><br><br>
        <label>Shift<br>
          <select name="shift">
            <option value="AM">AM</option>
            <option value="PM">PM</option>
          </select>
        </label><br><br>
        <label>Notes<br><textarea name="notes" rows="4" placeholder="Checklist items, hazards, visitors, etc."></textarea></label><br><br>
        <button type="submit">Save</button>
        <a href="/prestart">Cancel</a>
      </form>
    </body></html>
    """
    return page_auto(html)


@router.post("/new")
def create_prestart(
    ok: bool = Depends(require_writer),
    actor: str = Depends(current_actor),
    date: str = Form(...),
    shift: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    d = dt.date.fromisoformat(date)
    s = Shift(shift)
    # Enforce one-per-date+shift (UniqueConstraint exists)
    existing = db.scalar(select(Prestart).where(Prestart.date == d, Prestart.shift == s))
    if existing:
        # redirect to edit existing
        return RedirectResponse(url=f"/prestart/{existing.id}/edit", status_code=303)
    p = Prestart(date=d, shift=s, notes=notes.strip(), created_by=actor or "")
    db.add(p)
    db.flush()
    write_log(db, actor=actor, entity="Prestart", entity_id=p.id, action="CREATED", after_obj=p, summary=f"Prestart {d} {s.value}")
    db.commit()
    return RedirectResponse(url="/prestart", status_code=303)


@router.get("/{pid}/edit", response_class=HTMLResponse)
def edit_prestart_form(pid: int, ok: bool = Depends(require_writer), db: Session = Depends(get_db)):
    p = db.get(Prestart, pid)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    def sel(v: str) -> str:
        return "selected" if p.shift.value == v else ""
    html = f"""
    <html><body style="font-family: system-ui; max-width: 640px; margin: 2rem auto;">
      <h2>Edit Prestart</h2>
      <form method="post" action="/prestart/{p.id}/edit">
        <label>Date<br><input name="date" type="date" value="{p.date}"></label><br><br>
        <label>Shift<br>
          <select name="shift">
            <option value="AM" {sel('AM')}>AM</option>
            <option value="PM" {sel('PM')}>PM</option>
          </select>
        </label><br><br>
        <label>Notes<br><textarea name="notes" rows="6">{p.notes or ''}</textarea></label><br><br>
        <button type="submit">Save</button>
        <a href="/prestart">Cancel</a>
      </form>
    </body></html>
    """
    return page_auto(html)


@router.post("/{pid}/edit")
def edit_prestart(
    pid: int,
    ok: bool = Depends(require_writer),
    actor: str = Depends(current_actor),
    date: str = Form(...),
    shift: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    p = db.get(Prestart, pid)
    if not p:
        return RedirectResponse(url="/prestart", status_code=303)
    before = snapshot(p)
    p.date = dt.date.fromisoformat(date)
    p.shift = Shift(shift)
    p.notes = notes.strip()
    write_log(db, actor=actor, entity="Prestart", entity_id=p.id, action="UPDATED", before_obj=before, after_obj=p, summary=f"Prestart {p.date} {p.shift.value} updated")
    db.commit()
    return RedirectResponse(url="/prestart", status_code=303)


@router.get("/export.csv")
def export_prestart_csv(db: Session = Depends(get_db)):
    items = db.scalars(select(Prestart).order_by(Prestart.date.asc(), Prestart.shift.asc())).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "shift", "notes", "created_by"])
    for p in items:
        w.writerow([p.date, p.shift.value, (p.notes or "").replace("\n", " ").strip(), p.created_by or ""])
    buf.seek(0)
    headers = {"Content-Disposition": "attachment; filename=prestart_export.csv"}
    return StreamingResponse(iter([buf.read()]), media_type="text/csv", headers=headers)
