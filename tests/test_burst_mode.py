#!/usr/bin/env python3
"""เทสต์โหมดเร่งช่วงเปิดตัวคลิป — หน้าต่างเวลา, เพดานงบ, และระยะห่างการเขียน

รันด้วย:  ./web/venv/bin/python tests/test_burst_mode.py

สำคัญ: ตั้ง RUN_SCHEDULER=0 ก่อน import app เสมอ ไม่งั้น import เฉย ๆ จะสตาร์ท
scheduler จริงและไปแก้ชื่อคลิปจริง ไม่มีการยิงเน็ตในไฟล์นี้
"""
import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_TMP = tempfile.mkdtemp(prefix="viewtitle-burst-tests-")
os.environ["RUN_SCHEDULER"] = "0"
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "burst.db")
os.environ["QUOTA_STATE_PATH"] = os.path.join(_TMP, "burst-quota.json")
os.environ.setdefault("FLASK_SECRET", "test-secret")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))
import app  # noqa: E402

TZ = ZoneInfo("Asia/Bangkok")


def window(from_="2026-08-09T17:00", until="2026-08-09T20:00", reserve=600):
    """ตั้งหน้าต่างโหมดเร่งแล้วล้างสถานะที่ค้างจากเทสต์ก่อนหน้า"""
    app.BURST_FROM = from_
    app.BURST_UNTIL = until
    app.BURST_TZ = "Asia/Bangkok"
    app.BURST_READ_SECONDS = 30
    app.BURST_MIN_WRITE_SECONDS = 120
    app.BURST_RESERVE_UNITS = reserve
    app._last_write.clear()
    app._quota_cache = None
    app._write_quota_file(app._blank_quota_state())


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


def spend(units):
    app.quota_charge(units, "test")


def test_no_window_means_never_bursting():
    window(from_="", until="")
    assert app.burst_active(at("2026-08-09T18:00")) is False
    assert app.owner_interval_seconds(at("2026-08-09T18:00")) == \
        app.OWNER_INTERVAL_MINUTES * 60


def test_a_typo_in_the_time_does_not_take_the_app_down():
    """ค่า env ที่พิมพ์ผิดต้องแปลว่า 'ไม่เร่ง' ไม่ใช่ทำให้เว็บล่ม"""
    window(from_="ห้าโมงเย็น", until="2026-08-09T20:00")
    assert app.burst_active(at("2026-08-09T18:00")) is False


def test_only_inside_the_window():
    window()
    assert app.burst_active(at("2026-08-09T16:59")) is False, "ก่อนคลิปขึ้นไม่ต้องเร่ง"
    assert app.burst_active(at("2026-08-09T17:00")) is True, "ขอบเริ่มต้องนับว่าเร่ง"
    assert app.burst_active(at("2026-08-09T19:59")) is True
    assert app.burst_active(at("2026-08-09T20:00")) is False, "ขอบท้ายต้องหลุดโหมด"
    assert app.burst_active(at("2026-08-10T18:00")) is False, "คนละวันต้องไม่เร่ง"


def test_interval_switches_with_the_window():
    window()
    assert app.owner_interval_seconds(at("2026-08-09T18:00")) == 30
    assert app.owner_interval_seconds(at("2026-08-09T21:00")) == \
        app.OWNER_INTERVAL_MINUTES * 60


def test_burst_stops_when_the_budget_runs_low():
    """เลนเจ้าของไม่เช็คงบโดยตั้งใจ โหมดเร่งจึงต้องเช็คแทน ไม่งั้นดูดจนคนอื่นไม่เหลือ"""
    window(reserve=600)
    assert app.burst_active(at("2026-08-09T18:00")) is True
    spend(app.QUOTA_BUDGET - 599)          # เหลือ 599 ต่ำกว่า reserve
    assert app.burst_active(at("2026-08-09T18:00")) is False
    assert app.owner_interval_seconds(at("2026-08-09T18:00")) == \
        app.OWNER_INTERVAL_MINUTES * 60, "งบร่อยหรอแล้วต้องถอยกลับจังหวะปกติ"


def test_write_cooldown_only_applies_while_bursting():
    window()
    inside, outside = at("2026-08-09T18:00"), at("2026-08-09T21:00")
    app._mark_written("u1", inside)
    assert app.write_cooldown_left("u1", inside) > 0, "เพิ่งเขียนไปต้องรอ"
    assert app.write_cooldown_left("u1", outside) == 0, \
        "นอกโหมดเร่ง จังหวะมาจาก scheduler อยู่แล้ว ไม่ต้องคุมซ้อน"


def test_cooldown_expires_after_the_gap():
    window()
    base = at("2026-08-09T18:00")
    app._mark_written("u1", base)
    assert app.write_cooldown_left("u1", base + timedelta(seconds=119)) > 0
    assert app.write_cooldown_left("u1", base + timedelta(seconds=121)) == 0


def test_cooldown_is_per_user():
    window()
    now = at("2026-08-09T18:00")
    app._mark_written("u1", now)
    assert app.write_cooldown_left("u2", now) == 0, "คนหนึ่งเขียนไม่ควรบล็อกอีกคน"


def test_reads_stay_cheap_enough_for_a_whole_burst():
    """อ่านทุก 30 วิ 3 ชั่วโมง ต้องยังเหลืองบให้เขียนพอสมควร"""
    reads = 3 * 60 * 60 // 30
    assert reads * app.COST_VIDEOS_LIST == 360
    writes = (3 * 60) // 2                      # เขียนได้ถี่สุดทุก 2 นาที
    burst_cost = reads * app.COST_VIDEOS_LIST + writes * app.COST_VIDEOS_UPDATE
    assert burst_cost < app.QUOTA_BUDGET, burst_cost
    left = app.QUOTA_BUDGET - burst_cost
    assert left > 3500, f"เหลือให้ทั้งวันที่เหลือแค่ {left} หน่วย น้อยเกินไป"


def test_human_interval_switches_units():
    assert app.human_interval(30) == "30 s"
    assert app.human_interval(600) == "10 min"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} ผ่าน")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
