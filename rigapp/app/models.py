from __future__ import annotations

from datetime import datetime
from enum import Enum
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, ForeignKey, Float, Enum as SAEnum, Text
)

Base = declarative_base()

# --- Settings -----------------------------------------------------------------
class Settings(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    timezone = Column(String(64), default="UTC")
    reminder_horizon_days = Column(Integer, default=7)
    crew_pin_hash = Column(String(128), nullable=True)

# --- Stock / Restock ----------------------------------------------------------
class StockItem(Base):
    __tablename__ = "stock_items"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    on_rig_qty = Column(Integer, default=0)
    min_qty = Column(Integer, default=0)
    buffer_qty = Column(Integer, default=0)
    unit = Column(String(50), default="ea")
    location = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class RestockItem(Base):
    __tablename__ = "restock_items"
    id = Column(Integer, primary_key=True)
    stock_item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=True)  # linkable
    name = Column(String(200), nullable=False)
    qty = Column(Integer, default=1)
    unit = Column(String(50), default="ea")
    priority = Column(Integer, default=2)  # 1=high,2=med,3=low
    is_closed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    stock_item = relationship("StockItem", lazy="joined")

# --- Bits / Shrouds -----------------------------------------------------------
class BitStatus(str, Enum):
    NEW = "NEW"
    VERY_USED = "VERY_USED"
    NEEDS_RESHARPEN = "NEEDS_RESHARPEN"
    SHARPENED = "SHARPENED"
    EOL = "EOL"

class ShroudCondition(str, Enum):
    NEW = "NEW"
    GOOD = "GOOD"
    WORN = "WORN"
    EOL = "EOL"

class Shroud(Base):
    __tablename__ = "shrouds"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    condition = Column(SAEnum(ShroudCondition), default=ShroudCondition.NEW, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class Bit(Base):
    __tablename__ = "bits"
    id = Column(Integer, primary_key=True)
    serial = Column(String(120), nullable=False, unique=True)
    status = Column(SAEnum(BitStatus), default=BitStatus.NEW, nullable=False)
    size_mm = Column(Float, nullable=True)                 # live measurement
    life_meters_expected = Column(Integer, nullable=True)  # target life
    life_meters_used = Column(Integer, default=0)          # usage to date
    shroud_id = Column(Integer, ForeignKey("shrouds.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    shroud = relationship("Shroud", lazy="joined")

# --- Equipment & Faults -------------------------------------------------------
class Equipment(Base):
    __tablename__ = "equipment"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class EquipmentFault(Base):
    __tablename__ = "equipment_faults"
    id = Column(Integer, primary_key=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=True)
    equipment_name = Column(String(200), nullable=True)  # fallback if not linked
    description = Column(Text, nullable=False)
    is_resolved = Column(Boolean, default=False)
    priority = Column(Integer, default=2)  # 1=high,2=med,3=low
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    equipment = relationship("Equipment", backref="faults")

# --- Handover -----------------------------------------------------------------
class HandoverNote(Base):
    __tablename__ = "handover_notes"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=True)
    priority = Column(Integer, default=2)  # 1=high,2=med,3=low
    is_closed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    author = Column(String(120), nullable=True)

# --- Travel -------------------------------------------------------------------
class TravelLog(Base):
    __tablename__ = "travel_logs"
    id = Column(Integer, primary_key=True)
    person = Column(String(120), nullable=True)
    from_location = Column(String(200), nullable=False)
    to_location = Column(String(200), nullable=False)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

# --- Refuel -------------------------------------------------------------------
class RefuelLog(Base):
    __tablename__ = "refuel_logs"
    id = Column(Integer, primary_key=True)
    fuel_type = Column(String(80), nullable=True)           # e.g., Diesel
    amount_litres = Column(Float, nullable=True)            # numeric
    before_after_note = Column(String(200), nullable=True)  # short text
    notes = Column(Text, nullable=True)
    # NEW (optional fields used by calculator / prefill)
    tank_capacity_l = Column(Float, nullable=True)
    target_percent = Column(Integer, nullable=True)
    est_added_litres = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

# --- Usage (daily consumption etc.) ------------------------------------------
class UsageLog(Base):
    __tablename__ = "usage_logs"
    id = Column(Integer, primary_key=True)
    item_name = Column(String(200), nullable=False)
    qty = Column(Float, default=0.0)
    unit = Column(String(50), default="ea")
    notes = Column(Text, nullable=True)
    at_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

# --- Audit --------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    actor = Column(String(120), nullable=False)
    entity = Column(String(120), nullable=False)
    entity_id = Column(Integer, nullable=True)
    action = Column(String(120), nullable=False)
    summary = Column(Text, nullable=True)

# --- Jobs / Tasks -------------------------------------------------------------
class JobTask(Base):
    __tablename__ = "job_tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    notes = Column(Text, nullable=True)
    priority = Column(Integer, default=2)
    is_closed = Column(Boolean, default=False)
    is_done = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Fuel Watch (time-based) — when set, Jobs UI derives severity live
    is_fuel_watch = Column(Boolean, default=False)
    tank_capacity_l = Column(Float, nullable=True)      # litres
    start_percent = Column(Integer, nullable=True)      # 0-100 at start
    critical_percent = Column(Integer, nullable=True)   # when to flip to critical
    hourly_usage_lph = Column(Float, nullable=True)     # litres/hour
    started_at = Column(DateTime, nullable=True)        # when watch began

# --- Location hierarchy (Phase 6) --------------------------------------------

class LocationNode(Base):
    __tablename__ = "location_nodes"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    kind = Column(String(60), nullable=True)            # e.g. CONTAINER / BAY / SHELF / ROOM
    parent_id = Column(Integer, ForeignKey("location_nodes.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class StockLocationLink(Base):
    __tablename__ = "stock_location_links"
    id = Column(Integer, primary_key=True)
    stock_item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False, unique=True)  # one link per stock
    location_node_id = Column(Integer, ForeignKey("location_nodes.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
