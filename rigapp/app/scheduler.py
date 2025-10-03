# rigapp/app/scheduler.py
from __future__ import annotations

import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import select

from .db import _DATA_DIR, _session_for_rig  # reuse existing helpers
from .models import JobTask
from .audit import write_log


def _fuelwatch_effective_priority(t: JobTask) -> int:
    """
    Compute effective priority for Fuel Watch task:
      P0 (critical) if current% <= critical%
      P1 (high)     if current% <= critical% + 10
      else P2
    Falls back to stored priority if any data is missing.
    """
    ok = (
        t.is_fuel_watch
        and bool(t.started_at)
        and (t.tank_capacity_l or 0) > 0
        and (t.hourly_usage_lph or 0) >= 0
        and (t.start_percent is not None)
        and (t.critical_percent is not None)
    )
    if not ok:
        return t.priority or 2

    cap = float(t.tank_capacity_l or 0)
    start_pct = float(t.start_percent or 0)
    crit_pct = float(t.critical_percent or 25)
    use_lph = float(t.hourly_usage_lph or 0.0)

    hours = max(0.0, (datetime.utcnow() - t.started_at).total_seconds() / 3600.0)
    start_l = cap * (start_pct / 100.0)
    curr_l = max(0.0, start_l - (hours * use_lph))
    curr_pct = 0.0 if cap <= 0 else (curr_l / cap) * 100.0

    if curr_pct <= crit_pct:
        return 0
    if curr_pct <= crit_pct + 10:
        return 1
    return 2


def _fuelwatch_snapshot(t: JobTask) -> Optional[Tuple[int, Optional[float]]]:
    """(current_percent_int, hours_to_critical) or None."""
    ok = (
        t.is_fuel_watch
        and bool(t.started_at)
        and (t.tank_capacity_l or 0) > 0
        and (t.hourly_usage_lph or 0) >= 0
        and (t.start_percent is not None)
        and (t.critical_percent is not None)
    )
    if not ok:
        return None

    cap = float(t.tank_capacity_l or 0)
    start_pct = float(t.start_percent or 0)
    crit_pct = float(t.critical_percent or 25)
    use_lph = float(t.hourly_usage_lph or 0.0)

    hours = max(0.0, (datetime.utcnow() - t.started_at).total_seconds() / 3600.0)
    start_l = cap * (start_pct / 100.0)
    curr_l = max(0.0, start_l - (hours * use_lph))
    curr_pct = 0.0 if cap <= 0 else (curr_l / cap) * 100.0

    if use_lph <= 0:
        return (int(round(curr_pct)), None)

    crit_l = cap * (crit_pct / 100.0)
    hrs_to_crit = 0.0 if curr_l <= crit_l else (curr_l - crit_l) / use_lph
    return (int(round(curr_pct)), hrs_to_crit)


def _list_rig_ids() -> list[str]:
    """Detect DBs in data folder by filename (default.db, RC*.db => rig_id=stem)."""
    rigs: list[str] = []
    for p in Path(_DATA_DIR).glob("*.db"):
        rigs.append(p.stem)
    # Always ensure "default" is included (in case it doesn't exist yet)
    if "default" not in rigs:
        rigs.append("default")
    return sorted(set(rigs))


def _evaluate_jobs_for_rig(rig_id: str) -> None:
    """Escalate priorities for time-driven jobs (Fuel Watch) — escalate only, never de-escalate."""
    SessionLocal = _session_for_rig(rig_id)
    db = SessionLocal()
    try:
        tasks = db.scalars(select(JobTask).where(JobTask.is_closed == False)).all()  # noqa: E712

        changed = 0
        for t in tasks:
            if not t.is_fuel_watch:
                continue

            # Compute effective priority and only escalate (lower number is higher prio)
            eff = _fuelwatch_effective_priority(t)
            stored = t.priority if t.priority is not None else 2

            if eff < stored:
                old = stored
                t.priority = eff
                db.commit()
                changed += 1

                # Optional detail for log
                snap = _fuelwatch_snapshot(t)
                detail = ""
                if snap:
                    curr_pct, hrs_to_crit = snap
                    hrs_txt = "∞" if hrs_to_crit is None else f"{hrs_to_crit:.1f}h"
                    detail = f" (now ~{curr_pct}%, crit in {hrs_txt})"

                write_log(
                    db,
                    actor="system",
                    entity="jobtask",
                    entity_id=t.id,
                    action="auto-escalate",
                    summary=f"Priority {old} → {eff}: {t.title}{detail}",
                )

        if changed:
            # You could print server-side to see it working
            print(f"[scheduler] {rig_id}: escalated {changed} task(s).")
    finally:
        db.close()


async def start_scheduler(poll_seconds: int = 60) -> None:
    """
    Periodically scan each rig DB and escalate priorities for time-driven tasks.
    This runs forever; intended to be launched with asyncio.create_task(...) on app startup.
    """
    print("[scheduler] started, polling every", poll_seconds, "seconds")
    try:
        while True:
            for rig in _list_rig_ids():
                try:
                    _evaluate_jobs_for_rig(rig)
                except Exception as e:
                    # Keep ticking even if one rig fails
                    print(f"[scheduler] error on rig {rig}: {e}")
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:
        print("[scheduler] stopped")
        raise
