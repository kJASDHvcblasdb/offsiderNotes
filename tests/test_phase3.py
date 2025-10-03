import datetime as dt
import urllib.parse as up
import pytest
from sqlalchemy.exc import IntegrityError

from rigapp.app import models as m

# ---------- Helpers ----------

def create_settings(db):
    s = m.Settings(crew_pin_hash="hashed", timezone="Australia/Perth", reminder_horizon_days=14)
    db.add(s)
    db.commit()
    return s

# ---------- Tests ----------

def test_health_and_docs(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.text == "ok"

    # Swagger should load
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "paths" in r.json()

def test_settings_create_and_read(client, db_session):
    create_settings(db_session)
    r = client.get("/debug/settings")
    assert r.status_code == 200
    js = r.json()
    assert js["status"] == "ok"
    assert js["timezone"] == "Australia/Perth"
    assert js["reminder_horizon_days"] == 14
    assert js["has_pin_hash"] is True

def test_auth_redirect_and_access(client, db_session):
    create_settings(db_session)
    # Unauthed → redirect to /auth/pin?next=...
    r = client.get("/protected/test", follow_redirects=False)  # httpx
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/auth/pin")
    qs = dict(up.parse_qsl(up.urlsplit(loc).query))
    assert qs.get("next") == "/protected/test"

    # Simulate successful login by setting cookies
    client.cookies.set("crew_ok", "1")
    client.cookies.set("actor_name", "Tester")
    r2 = client.get("/protected/test")
    assert r2.status_code == 200
    assert "Protected OK" in r2.text

def test_stock_restock_linkage(client, db_session):
    create_settings(db_session)

    # Create stock item
    item = m.StockItem(name="RC Hammers", unit="pcs", on_rig_qty=2, min_qty=3, buffer_qty=1, critical=True)
    db_session.add(item)
    db_session.commit()

    # Create restock linked to the stock item
    restock = m.RestockItem(stock_item_id=item.id, requested_qty=2, priority=m.Priority.HIGH, notes="Low on rig")
    db_session.add(restock)
    db_session.commit()

    # Relationship check
    refreshed = db_session.get(m.StockItem, item.id)
    assert len(refreshed.restocks) == 1
    assert refreshed.restocks[0].requested_qty == 2
    assert refreshed.restocks[0].priority == m.Priority.HIGH

def test_bit_shroud_daily_usage(client, db_session):
    create_settings(db_session)

    bit = m.Bit(bit_number="BIT-137A", diameter_mm=137.0, prepared_by="Sam")
    sh_new = m.Shroud(size_mm=137.0, condition=m.ShroudCondition.NEW, at_rig=True)
    sh_used = m.Shroud(size_mm=137.0, condition=m.ShroudCondition.USED, at_rig=True)
    db_session.add_all([bit, sh_new, sh_used])
    db_session.commit()

    usage = m.DailyUsage(date=dt.date.today(), bit_id=bit.id, shroud_id=sh_used.id, prepared_by="Sam", notes="AM shift")
    db_session.add(usage)
    db_session.commit()

    # Backrefs
    bit_r = db_session.get(m.Bit, bit.id)
    sh_r = db_session.get(m.Shroud, sh_used.id)
    assert len(bit_r.usages) == 1
    assert len(sh_r.usages) == 1
    assert bit_r.usages[0].prepared_by == "Sam"

def test_prestart_unique_constraint(client, db_session):
    create_settings(db_session)
    today = dt.date.today()
    p1 = m.Prestart(date=today, shift=m.Shift.AM, notes="Morning")
    db_session.add(p1)
    db_session.commit()

    # Same date+shift should violate unique constraint
    p2 = m.Prestart(date=today, shift=m.Shift.AM, notes="Duplicate")
    db_session.add(p2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Different shift is fine
    p3 = m.Prestart(date=today, shift=m.Shift.PM, notes="Evening")
    db_session.add(p3)
    db_session.commit()
    assert p3.id is not None
