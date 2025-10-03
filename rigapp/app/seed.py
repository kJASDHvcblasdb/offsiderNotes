from __future__ import annotations
import os
import argparse
import datetime as dt
import bcrypt

from sqlalchemy import select

from .db import Base, engine, SessionLocal
from .models import (
    # Core / settings / audit
    Settings, Priority, ShroudCondition, Shift,
    # Rig
    Rig,
    # Stock & restock
    StockItem, RestockItem, ThresholdRule,
    # Bits / shrouds / usage
    Bit, BitStatus, Shroud, DailyUsage,
    # Notes / travel / refuel / prestart
    GeneralNote, TravelLog, RefuelLog, Prestart,
    # Equipment / faults / checks
    Equipment, EquipmentCheck, EquipmentFault,
    # Handovers
    Handover, HandoverItem,
)

# --------------------------
# Helpers
# --------------------------

def hash_pin(pin: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pin.encode("utf-8"), salt).decode("utf-8")

def seed_settings(db, pin: str, tz: str, horizon: int):
    existing = db.scalar(select(Settings).limit(1))
    if existing:
        existing.crew_pin_hash = hash_pin(pin)
        existing.timezone = tz
        existing.reminder_horizon_days = horizon
        return "Updated Settings row."
    else:
        s = Settings(
            id=1,
            crew_pin_hash=hash_pin(pin),
            timezone=tz,
            reminder_horizon_days=horizon,
        )
        db.add(s)
        return "Inserted Settings row."

# --------------------------
# Sample data (general)
# --------------------------

def seed_sample(db):
    # Rig
    rig = Rig(name="RC Rig 12", identifier="RC12", active=True)
    db.add(rig)

    # Stock (with usage/lead-time to exercise new calcs)
    rods = StockItem(name="Drill Rods", unit="pcs", on_rig_qty=20, min_qty=25, buffer_qty=5,
                     priority=Priority.HIGH, critical=True, usage_per_day=3, lead_time_days=2,
                     notes="Check collars")
    hammers = StockItem(name="RC Hammers", unit="pcs", on_rig_qty=2, min_qty=3, buffer_qty=1,
                        priority=Priority.HIGH, critical=True, usage_per_day=0.5, lead_time_days=5)
    diesel = StockItem(name="Diesel", unit="L", on_rig_qty=800.0, min_qty=600.0, buffer_qty=200.0,
                       priority=Priority.MEDIUM, usage_per_day=150.0, lead_time_days=1,
                       category="fuel")
    db.add_all([rods, hammers, diesel])
    db.flush()

    # Threshold override example
    db.add(ThresholdRule(stock_item_id=hammers.id, min_qty_override=4, buffer_qty_override=1, notes="Remote site buffer"))

    # Restock (open)
    db.add(RestockItem(stock_item_id=rods.id, requested_qty=10, priority=Priority.MEDIUM, notes="Bring on next changeout"))

    # Bits & shrouds baseline for demo (kept minimal; Apple notes seeded separately)
    bit = Bit(bit_number="BIT-137A", diameter_mm=137.0, status=BitStatus.NEW, notes="Fresh")
    shroud_new = Shroud(size_mm=137.0, condition=ShroudCondition.NEW, at_rig=True)
    shroud_used = Shroud(size_mm=137.0, condition=ShroudCondition.USED, at_rig=True)
    db.add_all([bit, shroud_new, shroud_used])
    db.flush()

    # Usage
    db.add(DailyUsage(date=dt.date.today(), bit_id=bit.id, shroud_id=shroud_used.id, meters_drilled=0.0,
                      prepared_by="Sam", notes="Morning shift"))

    # Notes / Travel / Refuel / Prestart
    db.add(GeneralNote(title="Cyclone liners", content="Check wear by Friday"))
    db.add(TravelLog(location_text="Karijini NP access rd", notes="Corrugations heavy"))
    db.add(RefuelLog(before_L=200.0, after_L=400.0, added_L=200.0, notes="Tank top-up",
                     next_due_at=dt.date.today() + dt.timedelta(days=2)))
    db.add(Prestart(date=dt.date.today(), shift=Shift.AM, notes="Heat plan in place", created_by="Cameron"))

    # Equipment / checks / faults
    comp = Equipment(name="Compressor X200", serial="COMPX200-77", notes="Keep eye on temps")
    db.add(comp); db.flush()
    db.add(EquipmentCheck(equipment_id=comp.id, status="OK", notes="No leaks"))
    db.add(EquipmentFault(equipment_id=comp.id, priority=Priority.HIGH, description="Intermittent overheat alarm"))

    # Handover
    ho = Handover(from_person="Alex", to_person="Jordan", notes="Night crew to monitor shroud wear")
    db.add(ho); db.flush()
    db.add_all([
        HandoverItem(handover_id=ho.id, title="Replace cyclone liners", priority=Priority.HIGH, comment="Order placed"),
        HandoverItem(handover_id=ho.id, title="Grease rod handler", priority=Priority.MEDIUM, comment="Due tomorrow"),
    ])

# --------------------------
# Apple Notes → Bits/Shrouds/Usage
# --------------------------

def seed_bits_from_notes(db):
    """
    Imports your Apple Notes entries into Bits/Shrouds/DailyUsage.

    Notes provided:
      - 13897-09, 143mm, 142 shroud, 24th September
      - 13897-08, 142mm, 141 shroud, 23rd September
      - 12748-1, EOL, debuttoned, 23rd September
      - 13670-27, 140 shroud, 19th September

    Assumptions:
      - Year is current project year (2025) and month is September.
      - Meters not specified → logged as 0.0 (can be edited later).
      - Expected life set to 4000 m as a starting point (adjust as needed).
    """
    def d(day: int) -> dt.date:
        # Use 2025-09-DD (project timeline)
        return dt.date(2025, 9, day)

    def ensure_shroud(size_mm: float):
        s = db.scalar(select(Shroud).where(Shroud.size_mm == size_mm).limit(1))
        if not s:
            s = Shroud(size_mm=size_mm, condition=ShroudCondition.NEW, at_rig=True)
            db.add(s); db.flush()
        return s

    # 1) 13897-09, 143mm, 142 shroud, 24th September
    s142 = ensure_shroud(142.0)
    b1 = db.scalar(select(Bit).where(Bit.bit_number == "13897-09").limit(1))
    if not b1:
        b1 = Bit(bit_number="13897-09", diameter_mm=143.0, status=BitStatus.USED, expected_life_m=4000, notes="")
        db.add(b1); db.flush()
    db.add(DailyUsage(date=d(24), bit_id=b1.id, shroud_id=s142.id, meters_drilled=0.0,
                      prepared_by="", notes="Logged from Apple note"))
    b1.last_used = d(24)

    # 2) 13897-08, 142mm, 141 shroud, 23rd September
    s141 = ensure_shroud(141.0)
    b2 = db.scalar(select(Bit).where(Bit.bit_number == "13897-08").limit(1))
    if not b2:
        b2 = Bit(bit_number="13897-08", diameter_mm=142.0, status=BitStatus.USED, expected_life_m=4000)
        db.add(b2); db.flush()
    db.add(DailyUsage(date=d(23), bit_id=b2.id, shroud_id=s141.id, meters_drilled=0.0,
                      prepared_by="", notes="Logged from Apple note"))
    b2.last_used = d(23)

    # 3) 12748-1, EOL, debuttoned, 23rd September
    b3 = db.scalar(select(Bit).where(Bit.bit_number == "12748-1").limit(1))
    if not b3:
        b3 = Bit(bit_number="12748-1", status=BitStatus.EOL, expected_life_m=4000, notes="debuttoned")
        db.add(b3); db.flush()
    # Link to a reasonable shroud if present (141 mm) for the log; fall back to 140 if needed
    sh_for_b3 = s141 or ensure_shroud(140.0)
    db.add(DailyUsage(date=d(23), bit_id=b3.id, shroud_id=sh_for_b3.id, meters_drilled=0.0,
                      prepared_by="", notes="EOL reason captured"))

    # 4) 13670-27, 140 shroud, 19th September (no diameter provided)
    s140 = ensure_shroud(140.0)
    b4 = db.scalar(select(Bit).where(Bit.bit_number == "13670-27").limit(1))
    if not b4:
        b4 = Bit(bit_number="13670-27", status=BitStatus.USED, expected_life_m=4000)
        db.add(b4); db.flush()
    db.add(DailyUsage(date=d(19), bit_id=b4.id, shroud_id=s140.id, meters_drilled=0.0,
                      prepared_by="", notes="Logged from Apple note"))
    b4.last_used = d(19)

# --------------------------
# Main
# --------------------------

def main():
    parser = argparse.ArgumentParser(description="Seed settings and optional sample data (incl. Apple Notes for bits/shrouds).")
    parser.add_argument("--pin", type=str, default=os.environ.get("CREW_PIN"), help="Crew PIN (or set CREW_PIN env var)")
    parser.add_argument("--tz", type=str, default=os.environ.get("RIG_TZ", "Australia/Perth"))
    parser.add_argument("--horizon", type=int, default=int(os.environ.get("REMINDER_HORIZON_DAYS", "14")))
    parser.add_argument("--with-sample", action="store_true", dest="with_sample", help="Also create sample rig/stock/bits/etc.")
    args = parser.parse_args()

    if not args.pin:
        raise SystemExit("Error: No PIN provided. Pass --pin 1234 or export CREW_PIN=1234")

    # Create tables
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        msg = seed_settings(db, args.pin, args.tz, args.horizon)
        db.commit()

    if args.with_sample:
        with SessionLocal() as db:
            seed_sample(db)
            # Import Apple Notes-based bits/shrouds/usage
            seed_bits_from_notes(db)
            db.commit()
            msg += " + Inserted sample data + Apple Notes bits."

    print(msg)
    print(f"Timezone: {args.tz}  |  Reminder horizon: {args.horizon} days")
    if args.with_sample:
        print("Sample data created: rig, stock items (usage/lead-time), restock, baseline bits/shrouds, Apple Notes bits/shrouds/usage, logs, equipment, handover.")

if __name__ == "__main__":
    main()
