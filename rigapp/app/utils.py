from __future__ import annotations
from typing import Tuple, Optional
import datetime as dt
from sqlalchemy.orm import Session
from sqlalchemy import select
from .models import StockItem, ThresholdRule

def effective_thresholds(db: Session, item: StockItem) -> Tuple[float, float]:
    rule = db.scalar(select(ThresholdRule).where(ThresholdRule.stock_item_id == item.id).limit(1))
    eff_min = rule.min_qty_override if (rule and rule.min_qty_override is not None) else item.min_qty
    eff_buf = rule.buffer_qty_override if (rule and rule.buffer_qty_override is not None) else item.buffer_qty
    return float(eff_min or 0), float(eff_buf or 0)

def compute_days_cover(item: StockItem) -> Optional[float]:
    if not item.usage_per_day or item.usage_per_day <= 0:
        return None
    return float(item.on_rig_qty or 0) / float(item.usage_per_day)

def compute_days_until_min(db: Session, item: StockItem) -> Optional[float]:
    if not item.usage_per_day or item.usage_per_day <= 0:
        return None
    eff_min, _ = effective_thresholds(db, item)
    return (float(item.on_rig_qty or 0) - eff_min) / float(item.usage_per_day)

def eta_date(days: Optional[float]) -> Optional[str]:
    if days is None:
        return None
    # round down to whole days for display; avoid negatives in formatting
    target = dt.date.today() + dt.timedelta(days=max(0, int(days)))
    return target.isoformat()

def stock_status(db: Session, item: StockItem) -> str:
    """
    'critical' | 'low' | 'watch' | 'ok'
    critical: usage known and days_until_min <= lead_time_days
    low: on_rig_qty < eff_min
    watch: on_rig_qty < eff_min + eff_buf
    ok: else
    """
    eff_min, eff_buf = effective_thresholds(db, item)
    qty = float(item.on_rig_qty or 0)
    # critical check (only if we know usage and lead time)
    if item.usage_per_day and item.usage_per_day > 0 and item.lead_time_days and item.lead_time_days > 0:
        dmin = compute_days_until_min(db, item)
        if dmin is not None and dmin <= item.lead_time_days:
            return "critical"
    if qty < eff_min:
        return "low"
    if qty < eff_min + eff_buf:
        return "watch"
    return "ok"

def parse_float(val: Optional[str]) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except Exception:
        return None
