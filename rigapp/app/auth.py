from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

# Cookie names
ACTOR_COOKIE = "offsider_actor"
RIG_ID_COOKIE = "offsider_rig"
RIG_TITLE_COOKIE = "offsider_rig_title"

# defaults
DEFAULT_ACTOR = ""
RIGS_JSON_PATH = Path(__file__).parent / "data" / "rigs.JSON"

router = APIRouter(prefix="/auth", tags=["auth"])


# --------------------------- helpers -----------------------------------------

def _hash_pin(pin: str) -> str:
    return hashlib.sha256((pin or "").encode("utf-8")).hexdigest()


def _coerce_rig(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize various keys from rigs.json into a single shape we use:
      - id (required)
      - title (fallback: name or id)
      - subtitle (fallback: quote)
      - pin (optional; stripped)
    """
    rid = (obj.get("id") or "").strip()
    title = (obj.get("title") or obj.get("name") or rid).strip()
    subtitle = (obj.get("subtitle") or obj.get("quote") or "").strip()
    pin = obj.get("pin")
    if isinstance(pin, str):
        pin = pin.strip()
    return {"id": rid, "title": title, "subtitle": subtitle, "pin": pin}


def _load_rigs() -> List[Dict[str, Any]]:
    """
    Accepts either:
      {
        "rigs": [ {...}, {...} ]
      }
    or a top-level list:
      [ {...}, {...} ]
    """
    if not RIGS_JSON_PATH.exists():
        return []
    try:
        raw = json.loads(RIGS_JSON_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            items = raw.get("rigs") or []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []

        rigs: List[Dict[str, Any]] = []
        for r in items:
            if not isinstance(r, dict):
                continue
            rig = _coerce_rig(r)
            if not rig["id"]:
                continue
            rigs.append(rig)
        return rigs
    except Exception:
        return []


def _find_rig(rig_id: str) -> Optional[Dict[str, Any]]:
    rid = (rig_id or "").strip()
    for r in _load_rigs():
        if r["id"] == rid:
            return r
    return None


# --------------------------- dependencies ------------------------------------

def current_actor(request: Request) -> str:
    # empty string means "not signed in"
    return request.cookies.get(ACTOR_COOKIE, DEFAULT_ACTOR)


def current_rig_id(request: Request) -> str:
    return request.cookies.get(RIG_ID_COOKIE, "")


def current_rig_title(request: Request) -> str:
    return request.cookies.get(RIG_TITLE_COOKIE, "")


def require_reader(
    actor: str = Depends(current_actor),
    rig_id: str = Depends(current_rig_id),
) -> bool:
    """
    Enforce login on routes that depend on it.
    If not signed in, redirect to the rig selection/sign-in flow.
    """
    if not actor or not rig_id:
        # Raising HTTPException with 303 + Location triggers a redirect
        raise HTTPException(status_code=303, headers={"Location": "/auth/select"})
    return True


# --------------------------- UI: pick rig + login ----------------------------

@router.get("/select", response_class=HTMLResponse, name="rig_select")
def select_rig(request: Request) -> HTMLResponse:
    rigs = _load_rigs()
    if not rigs:
        return HTMLResponse(
            "<html><head><link rel='stylesheet' href='/static/style.css'></head>"
            "<body class='container'>"
            "<h1>No rigs configured</h1>"
            "<p class='muted'>Add rigs to <code>rigapp/app/data/rigs.JSON</code> and reload.</p>"
            "</body></html>",
            status_code=500,
        )

    # list of rigs with a “Sign in” button
    items = []
    for r in rigs:
        rid = r["id"]
        title = r["title"]
        subtitle = r["subtitle"]
        items.append(
            "<div class='card'>"
            f"<strong>{title}</strong>"
            f"{(' <span class=\"muted\">' + subtitle + '</span>') if subtitle else ''}"
            "<div style='margin-top:0.5rem'>"
            f"<a class='btn' href='/auth/login?rig={rid}'>Sign in</a>"
            "</div>"
            "</div>"
        )

    html = (
        "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<link rel='stylesheet' href='/static/style.css'><title>Select Rig</title></head>"
        "<body class='container'>"
        "<h1>Select Rig</h1>"
        f"<div class='cards'>{''.join(items)}</div>"
        "</body></html>"
    )
    return HTMLResponse(html)


@router.get("/login", response_class=HTMLResponse, name="login_form")
def login_form(rig: str = Query("")) -> HTMLResponse:
    rig_obj = _find_rig(rig) if rig else None
    if not rig_obj:
        # unknown or missing: go choose
        return RedirectResponse("/auth/select", status_code=303)

    title = rig_obj["title"]
    html = (
        "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<link rel='stylesheet' href='/static/style.css'><title>Sign in</title></head>"
        "<body class='container'>"
        f"<h1>Sign in — {title}</h1>"
        "<form method='post' action='/auth/login' class='form'>"
        f"<input type='hidden' name='rig_id' value='{rig_obj['id']}'>"
        f"<input type='hidden' name='rig_title' value='{title}'>"
        "<label>Crew name <input name='actor' required maxlength='80' placeholder='e.g., Cam'></label>"
        "<label>PIN <input type='password' name='pin' required maxlength='20'></label>"
        "<div class='actions'>"
        "<button class='btn' type='submit'>Sign in</button>"
        "<a class='btn' href='/auth/select'>Cancel</a>"
        "</div>"
        "</form>"
        "</body></html>"
    )
    return HTMLResponse(html)


@router.post("/login", response_class=HTMLResponse, name="login_post")
def login_post(
    actor: str = Form(...),
    rig_id: str = Form(...),
    rig_title: str = Form(""),
    pin: str = Form(...),
):
    r = _find_rig(rig_id)
    if not r:
        return RedirectResponse("/auth/select", status_code=303)

    expected_pin = (r.get("pin") or "").strip()
    provided_pin = (pin or "").strip()

    # If a PIN is set in rigs.json, enforce it; if empty/missing, accept any PIN.
    if expected_pin and provided_pin != expected_pin:
        html = (
            "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<link rel='stylesheet' href='/static/style.css'><title>Sign in</title></head>"
            "<body class='container'>"
            "<p class='danger'>Invalid PIN.</p>"
            f"<p><a class='btn' href='/auth/login?rig={rig_id}'>Try again</a></p>"
            "</body></html>"
        )
        return HTMLResponse(html, status_code=401)

    # success: set cookies
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(ACTOR_COOKIE, actor, httponly=True, samesite="lax")
    resp.set_cookie(RIG_ID_COOKIE, r["id"], httponly=True, samesite="lax")
    resp.set_cookie(RIG_TITLE_COOKIE, rig_title or r["title"] or r["id"], httponly=True, samesite="lax")
    return resp


@router.post("/logout", name="logout")
def logout() -> Response:
    resp = RedirectResponse("/auth/select", status_code=303)
    resp.delete_cookie(ACTOR_COOKIE)
    resp.delete_cookie(RIG_ID_COOKIE)
    resp.delete_cookie(RIG_TITLE_COOKIE)
    return resp
