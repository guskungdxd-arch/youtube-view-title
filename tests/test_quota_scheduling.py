#!/usr/bin/env python3
"""เทสต์การนับโควตา / เลนเจ้าของ / interval ปรับตัว / การอ่านแบบ batch

รันด้วย:  ./web/venv/bin/python tests/test_quota_scheduling.py
(pytest ก็รันได้ ถ้ามีติดตั้ง — ฟังก์ชัน test_* ใช้ assert ธรรมดา)

สำคัญ: ตั้ง RUN_SCHEDULER=0 + ชี้ DATABASE_PATH/QUOTA_STATE_PATH ไปที่ temp
ก่อน import app เสมอ ไม่งั้น import เฉย ๆ จะสตาร์ท scheduler จริงและไปแก้ชื่อ
คลิปจริงของเจ้าของ ทุกการเรียก API ในไฟล์นี้เป็นของปลอมทั้งหมด
"""
import copy
import json
import os
import sqlite3
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_TMP = tempfile.mkdtemp(prefix="viewtitle-tests-")
os.environ["RUN_SCHEDULER"] = "0"
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "import.db")
os.environ["QUOTA_STATE_PATH"] = os.path.join(_TMP, "import-quota.json")
os.environ.setdefault("FLASK_SECRET", "test-secret")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))
import app  # noqa: E402

assert app.scheduler is None, "scheduler ต้องไม่ถูกสตาร์ทตอน import (RUN_SCHEDULER=0)"

PACIFIC = ZoneInfo("America/Los_Angeles")


# --------------------------------------------------------------- YouTube ปลอม
class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeVideos:
    def __init__(self, api, sub):
        self.api, self.sub = api, sub

    def list(self, part, id):  # noqa: A002 - ชื่อพารามิเตอร์ตามของ googleapiclient
        ids = id.split(",")
        self.api.list_calls.append({"sub": self.sub, "part": part, "ids": ids})
        if self.sub in self.api.broken_readers:
            return FakeRequest(RuntimeError(f"token ของ {self.sub} ใช้ไม่ได้"))
        items = []
        for vid in ids:
            item = self.api.videos.get(vid)
            if item is None:
                continue  # ไม่มีคลิปนี้
            owner = self.api.private.get(vid)
            if owner is not None and owner != self.sub:
                continue  # คลิป private — token คนอื่นมองไม่เห็น
            items.append(copy.deepcopy(item))
        return FakeRequest({"items": items})

    def update(self, part, body):
        self.api.update_calls.append({"sub": self.sub, "part": part, "body": body})
        return FakeRequest({"id": body.get("id")})


class FakeYouTube:
    def __init__(self, api, sub):
        self._videos = FakeVideos(api, sub)

    def videos(self):
        return self._videos


class FakeAPI:
    """เก็บคลิปปลอม + บันทึกทุก call ที่ถูกยิง"""

    def __init__(self):
        self.videos = {}
        self.private = {}          # video_id -> sub ที่มองเห็นได้คนเดียว
        self.broken_readers = set()
        self.list_calls = []
        self.update_calls = []

    def add_video(self, vid, title, views, description="desc", tags=None,
                  category="22", private_to=None, include_category=True):
        snippet = {"title": title, "description": description,
                   "tags": list(tags if tags is not None else ["a", "b"])}
        if include_category:
            snippet["categoryId"] = category
        self.videos[vid] = {"id": vid, "snippet": snippet,
                            "statistics": {"viewCount": str(views)}}
        if private_to:
            self.private[vid] = private_to

    def client_for(self, row):
        return FakeYouTube(self, row["sub"])


class FakeJob:
    def __init__(self):
        self.calls = []

    def reschedule(self, trigger, **kwargs):
        self.calls.append((trigger, kwargs))

    @property
    def last_minutes(self):
        return self.calls[-1][1]["minutes"] if self.calls else None


# ------------------------------------------------------------------ harness
_case = 0


def setup(users, clock=None, budget=9000, admin_emails=None):
    """สร้าง DB + ไฟล์โควตาใหม่ต่อเทสต์ และเสียบ YouTube ปลอมแทนของจริง"""
    global _case
    _case += 1
    app.DB_PATH = os.path.join(_TMP, f"case{_case}.db")
    app.QUOTA_STATE_PATH = os.path.join(_TMP, f"case{_case}-quota.json")
    app._quota_cache = None
    app.QUOTA_BUDGET = budget
    app.ADMIN_EMAILS = list(admin_emails or [])
    app.MIN_INTERVAL_MINUTES = 30
    app.MAX_INTERVAL_MINUTES = 360
    app.OWNER_INTERVAL_MINUTES = 10
    app.init_db()
    with app.db() as conn:
        for u in users:
            conn.execute(
                "INSERT INTO users (sub, email, credentials, video_id, title_template,"
                " enabled, last_status) VALUES (?,?,?,?,?,?,?)",
                (u["sub"], u.get("email", u["sub"] + "@example.com"),
                 json.dumps({"token": "fake"}), u.get("video_id"),
                 u.get("title_template", "views {views}"), u.get("enabled", 1), ""),
            )
    api = FakeAPI()
    app._youtube_for_row = api.client_for
    app.shared_job = FakeJob()
    app.owner_job = FakeJob()
    if clock is None:
        clock = datetime(2026, 7, 25, 12, 0, tzinfo=PACIFIC)
    set_clock(clock)
    return api


def set_clock(when):
    """ตรึงนาฬิกา (ไม่ sleep) — รับ datetime ที่มี tzinfo"""
    assert when.tzinfo is not None
    app._now_pacific = lambda: when.astimezone(PACIFIC)
    app._quota_cache = None  # บังคับให้อ่านสถานะใหม่ตามวันที่ใหม่


def statuses():
    with app.db() as conn:
        return {r["sub"]: r["last_status"] for r in conn.execute("SELECT * FROM users")}


def set_used(units):
    with app._quota_lock:
        state = app.quota_state()
        state["used"] = units
        app._write_quota_file(state)


def set_last_shared_cost(units):
    with app._quota_lock:
        state = app.quota_state()
        state["last_shared_cost"] = units
        app._write_quota_file(state)


# -------------------------------------------------------------------- เทสต์
def test_cost_of_run_that_changes_titles():
    """N ชื่อเปลี่ยน = N*50 + ค่าอ่าน (batch เดียว = 1 หน่วย)"""
    users = [{"sub": f"u{i}", "video_id": f"v{i}"} for i in range(3)]
    api = setup(users, admin_emails=["nobody@example.com"])
    for i in range(3):
        api.add_video(f"v{i}", "ชื่อเก่า", 100 + i)

    spent = app.run_shared()

    assert len(api.list_calls) == 1, api.list_calls
    assert len(api.update_calls) == 3
    assert spent == 3 * 50 + 1 == 151, spent
    assert app.quota_used() == 151
    assert all(s.startswith("✅") for s in statuses().values()), statuses()


def test_cost_of_run_that_changes_nothing():
    """ชื่อตรงอยู่แล้ว = ไม่ยิง update เลย จ่ายแค่ค่าอ่าน"""
    users = [{"sub": f"u{i}", "video_id": f"v{i}"} for i in range(3)]
    api = setup(users, admin_emails=["nobody@example.com"])
    for i in range(3):
        api.add_video(f"v{i}", f"views {100 + i:,}", 100 + i)

    spent = app.run_shared()

    assert len(api.list_calls) == 1
    assert api.update_calls == [], api.update_calls
    assert spent == 1, spent
    assert all("ชื่อไม่เปลี่ยน" in s for s in statuses().values()), statuses()


def test_snippet_is_preserved_on_update():
    """videos.update ทับ snippet ทั้งก้อน — body ต้องพา description/tags/categoryId ไปด้วย"""
    users = [{"sub": "u1", "video_id": "v1"}, {"sub": "u2", "video_id": "v2"}]
    api = setup(users, admin_emails=["nobody@example.com"])
    api.add_video("v1", "ชื่อเก่า", 5, description="คำบรรยายยาว ๆ",
                  tags=["cat", "dog"], category="27")
    # คลิปที่ API ไม่ส่ง categoryId มา ต้อง default เป็น "22" (API บังคับต้องมี)
    api.add_video("v2", "ชื่อเก่า", 6, description="", tags=[], include_category=False)

    app.run_shared()

    by_id = {c["body"]["id"]: c["body"] for c in api.update_calls}
    assert set(by_id) == {"v1", "v2"}, by_id
    s1 = by_id["v1"]["snippet"]
    assert s1["title"] == "views 5"
    assert s1["description"] == "คำบรรยายยาว ๆ", s1
    assert s1["tags"] == ["cat", "dog"], s1
    assert s1["categoryId"] == "27", s1
    assert by_id["v2"]["snippet"]["categoryId"] == "22", by_id["v2"]
    assert by_id["v2"]["snippet"]["tags"] == []
    # แต่ละคลิปต้องได้ snippet ของตัวเอง ไม่ใช่ของเพื่อนในก้อนเดียวกัน
    assert by_id["v2"]["snippet"]["description"] == ""
    assert all(c["part"] == "snippet" for c in api.update_calls)
    # update ต้องยิงด้วย token ของเจ้าของคลิปเอง
    assert {c["sub"] for c in api.update_calls} == {"u1", "u2"}


def test_batching_120_users_makes_3_reads():
    users = [{"sub": f"u{i}", "video_id": f"v{i}"} for i in range(120)]
    api = setup(users, admin_emails=["nobody@example.com"])
    for i in range(120):
        api.add_video(f"v{i}", f"views {i:,}", i)  # ชื่อตรงแล้ว → ไม่มี update

    spent = app.run_shared()

    assert len(api.list_calls) == 3, len(api.list_calls)
    assert [len(c["ids"]) for c in api.list_calls] == [50, 50, 20]
    assert api.update_calls == []
    assert spent == 3, spent


def test_batch_read_falls_back_for_private_video():
    """คลิป private ของคนอื่นจะไม่กลับมาในก้อน → ต้องอ่านซ้ำด้วย token เจ้าตัว"""
    users = [{"sub": "u1", "video_id": "v1"}, {"sub": "u2", "video_id": "v2"}]
    api = setup(users, admin_emails=["nobody@example.com"])
    api.add_video("v1", "ชื่อเก่า", 10)
    api.add_video("v2", "ชื่อเก่า", 20, private_to="u2")

    spent = app.run_shared()

    assert len(api.list_calls) == 2, api.list_calls
    assert api.list_calls[0]["ids"] == ["v1", "v2"]      # ก้อนแรกอ่านด้วย u1
    assert api.list_calls[0]["sub"] == "u1"
    assert api.list_calls[1] == {"sub": "u2", "part": "snippet,statistics",
                                "ids": ["v2"]}          # อ่านซ้ำด้วย u2 เอง
    assert len(api.update_calls) == 2
    assert spent == 2 + 2 * 50, spent
    assert all(s.startswith("✅") for s in statuses().values()), statuses()


def test_missing_video_still_reports_not_found():
    users = [{"sub": "u1", "video_id": "zzzMissing1"}]
    api = setup(users, admin_emails=["nobody@example.com"])

    spent = app.run_shared()

    assert [c["ids"] for c in api.list_calls] == [["zzzMissing1"], ["zzzMissing1"]]
    assert api.update_calls == []
    assert statuses()["u1"] == app.STATUS_NOT_FOUND
    assert app.status_is_error(app.STATUS_NOT_FOUND)
    assert spent == 2


def test_broken_reader_falls_back_to_per_user_reads():
    users = [{"sub": "u1", "video_id": "v1"}, {"sub": "u2", "video_id": "v2"}]
    api = setup(users, admin_emails=["nobody@example.com"])
    api.add_video("v1", "views 1", 1)
    api.add_video("v2", "views 2", 2)
    api.broken_readers.add("u1")

    app.run_shared()

    # u1 พัง → ลองคนที่สอง (u2) อ่านก้อนสำเร็จ ไม่ต้องอ่านรายคน
    assert [c["sub"] for c in api.list_calls] == ["u1", "u2"]
    assert statuses() == {"u1": "วิว 1 — ชื่อไม่เปลี่ยน", "u2": "วิว 2 — ชื่อไม่เปลี่ยน"}


def test_weird_video_id_cannot_pollute_a_shared_batch():
    """video_id ที่มี comma ต้องไม่ถูกยัดเข้าก้อนรวมของคนอื่น"""
    users = [{"sub": "u1", "video_id": "v1,v2"}, {"sub": "u2", "video_id": "v2"}]
    api = setup(users, admin_emails=["nobody@example.com"])
    api.add_video("v1", "ชื่อเก่า", 1)
    api.add_video("v2", "views 2", 2)

    app.run_shared()

    assert [c["ids"] for c in api.list_calls] == [["v2"], ["v1", "v2"]], api.list_calls
    assert api.list_calls[0]["sub"] == "u2"       # ก้อนรวมมีแค่ id ที่ถูกต้อง
    assert api.list_calls[1]["sub"] == "u1"       # ค่าเพี้ยนไปอ่านรายคนของตัวเอง
    assert api.update_calls == [], api.update_calls  # v1 ไม่ได้ถูกอัปเดตในนามของ u1
    assert statuses()["u1"] == app.STATUS_NOT_FOUND
    assert statuses()["u2"] == "วิว 2 — ชื่อไม่เปลี่ยน"


def test_only_configured_enabled_users_cost_quota():
    users = [
        {"sub": "on", "video_id": "v1"},
        {"sub": "no-video", "video_id": None},
        {"sub": "blank-video", "video_id": ""},
        {"sub": "off", "video_id": "v9", "enabled": 0},
    ]
    api = setup(users, admin_emails=["nobody@example.com"])
    api.add_video("v1", "ชื่อเก่า", 7)
    api.add_video("v9", "ชื่อเก่า", 9)

    assert {r["sub"] for r in app.quota_rows()} == {"on"}
    spent = app.run_shared()
    assert [c["ids"] for c in api.list_calls] == [["v1"]]
    assert spent == 51
    assert statuses()["off"] == "" and statuses()["no-video"] == ""


def test_quota_resets_at_pacific_midnight_not_utc():
    api = setup([{"sub": "u1", "video_id": "v1"}],
                clock=datetime(2026, 7, 24, 23, 59, tzinfo=PACIFIC),
                admin_emails=["nobody@example.com"])
    api.add_video("v1", "ชื่อเก่า", 1)

    app.quota_charge(200)
    assert app.quota_used() == 200
    assert app.quota_state()["date"] == "2026-07-24"

    # เที่ยงคืน UTC (= 17:05 แปซิฟิก) ยัง **ไม่** รีเซ็ต
    set_clock(datetime(2026, 7, 25, 0, 5, tzinfo=timezone.utc))
    assert app._pacific_date() == "2026-07-24"
    assert app.quota_used() == 200, "รีเซ็ตตามเวลา UTC = ผิด"

    # ข้ามเที่ยงคืนแปซิฟิก → รีเซ็ต
    set_clock(datetime(2026, 7, 25, 0, 1, tzinfo=PACIFIC))
    assert app.quota_used() == 0
    with open(app.QUOTA_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["date"] == "2026-07-25"


def test_minutes_until_reset_is_pacific():
    setup([], admin_emails=["nobody@example.com"])
    assert round(app.minutes_until_quota_reset(
        datetime(2026, 7, 25, 23, 0, tzinfo=PACIFIC))) == 60
    assert round(app.minutes_until_quota_reset(
        datetime(2026, 7, 25, 12, 0, tzinfo=PACIFIC))) == 720
    # เวลา UTC ต้องถูกแปลงก่อน: 07:00 UTC = 00:00 PDT → เหลือเต็มวัน
    assert round(app.minutes_until_quota_reset(
        datetime(2026, 7, 25, 7, 0, tzinfo=timezone.utc))) == 1440


def test_counter_survives_process_restart():
    setup([], admin_emails=["nobody@example.com"])
    app.quota_charge(app.COST_VIDEOS_LIST)
    app.quota_charge(app.COST_VIDEOS_UPDATE)
    assert app.quota_used() == 51

    app._quota_cache = None  # เหมือน deploy/restart แล้ว process ใหม่
    assert app.quota_used() == 51, "ตัวนับต้องรอดข้าม restart"

    app._quota_cache = None
    with open(app.QUOTA_STATE_PATH, "w", encoding="utf-8") as f:
        f.write("{ ไฟล์พัง")
    assert app.quota_used() == 0, "ไฟล์พังต้องไม่ทำให้แอปล้ม"


def test_interval_clamps_at_both_ends():
    setup([], budget=9000, admin_emails=["nobody@example.com"])
    now = datetime(2026, 7, 25, 12, 0, tzinfo=PACIFIC)  # เหลือ 720 นาที

    set_used(0)  # เหลือ 9000 หน่วย, รอบก่อนใช้ 1 → 720*1/9000 = 0.08 นาที
    assert app.compute_shared_interval(1, now) == app.MIN_INTERVAL_MINUTES == 30

    set_used(8990)  # เหลือ 10 หน่วย, รอบก่อนใช้ 151 → 720*151/10 = 10872 นาที
    assert app.compute_shared_interval(151, now) == app.MAX_INTERVAL_MINUTES == 360

    set_used(9000)  # ไม่เหลือเลย → หารด้วย 1 → ชนเพดาน
    assert app.compute_shared_interval(51, now) == 360

    set_used(8000)  # เหลือ 1000, รอบก่อน 51 → 720*51/1000 = 36.7 → ceil 37
    assert app.compute_shared_interval(51, now) == 37

    set_used(0)  # ไม่มีใครกินโควตา → ไม่มีเหตุให้ยืด
    assert app.compute_shared_interval(0, now) == 30


def test_shared_run_reschedules_the_job():
    users = [{"sub": f"u{i}", "video_id": f"v{i}"} for i in range(3)]
    api = setup(users, admin_emails=["nobody@example.com"])
    for i in range(3):
        api.add_video(f"v{i}", "ชื่อเก่า", i)
    set_used(8000)
    set_clock(datetime(2026, 7, 25, 12, 0, tzinfo=PACIFIC))

    app.run_shared()

    # ใช้ไป 151 → เหลือ 9000-8151=849, เวลาเหลือ 720 นาที → 720*151/849 = 128.1 → 129
    assert app.shared_job.calls, "ต้องเรียก job.reschedule จริง ไม่ใช่แค่คำนวณ"
    assert app.shared_job.calls[-1][0] == "interval"
    assert app.shared_job.last_minutes == 129, app.shared_job.last_minutes
    assert app.shared_interval_minutes() == 129
    assert app.quota_state()["last_shared_cost"] == 151


def test_shared_run_skipped_when_budget_cannot_fund_one_run():
    users = [{"sub": f"u{i}", "video_id": f"v{i}"} for i in range(3)]
    api = setup(users, budget=9000, admin_emails=["nobody@example.com"])
    for i in range(3):
        api.add_video(f"v{i}", "ชื่อเก่า", i)
    set_used(8900)            # เหลือ 100
    set_last_shared_cost(151)  # รอบก่อนใช้ 151 → เหลือไม่พอ

    spent = app.run_shared()

    assert spent == 0
    assert api.list_calls == [], "ห้ามยิง API เลยตอนโควตาไม่พอ"
    assert api.update_calls == []
    assert app.quota_used() == 8900
    assert set(statuses().values()) == {""}, statuses()
    assert app.shared_job.last_minutes >= 5      # ตื่นมาเช็คใหม่หลังโควตารีเซ็ต
    # การข้ามรอบไม่ทับ interval ที่คำนวณไว้ใน state (persist=False)
    assert app.quota_state()["shared_interval"] == 30


def test_owner_lane_runs_even_when_budget_is_gone():
    users = [
        {"sub": "owner", "email": "owner@example.com", "video_id": "v-owner"},
        {"sub": "u2", "email": "u2@example.com", "video_id": "v2"},
    ]
    api = setup(users, budget=9000, admin_emails=["owner@example.com"])
    api.add_video("v-owner", "ชื่อเก่า", 111)
    api.add_video("v2", "ชื่อเก่า", 222)
    set_used(9000)             # งบหมดเกลี้ยง
    set_last_shared_cost(51)

    shared_spent = app.run_shared()
    assert shared_spent == 0 and api.list_calls == []

    owner_spent = app.run_owner()
    assert [c["ids"] for c in api.list_calls] == [["v-owner"]], api.list_calls
    assert [c["body"]["id"] for c in api.update_calls] == ["v-owner"]
    assert owner_spent == 51
    assert statuses()["owner"].startswith("✅")
    assert statuses()["u2"] == "", "เลนรวมต้องไม่ถูกรันตอนงบหมด"


def test_owner_resolution_matches_is_admin():
    users = [
        {"sub": "first", "email": "first@example.com", "video_id": "v1"},
        {"sub": "second", "email": "boss@example.com", "video_id": "v2"},
    ]
    setup(users, admin_emails=["boss@example.com"])
    assert app.owner_subs() == {"second"}
    with app.db() as conn:
        rows = {r["sub"]: r for r in conn.execute("SELECT * FROM users")}
    assert app.is_admin(rows["second"]) and not app.is_admin(rows["first"])

    app.ADMIN_EMAILS = []  # ไม่ตั้ง ADMIN_EMAILS → คนแรกใน users คือเจ้าของ
    assert app.owner_subs() == {"first"}
    assert app.is_admin(rows["first"]) and not app.is_admin(rows["second"])

    # คืนเป็นข้อความพร้อมหน่วย เพราะเลนเจ้าของสลับเป็นวินาทีได้ตอนโหมดเร่ง
    assert app.interval_for_user(rows["first"]) == (
        app.human_interval(app.owner_interval_seconds()), False
    )
    assert app.interval_for_user(rows["second"]) == (
        f"{app.shared_interval_minutes()} min", True
    )


def test_run_now_path_keeps_statuses():
    users = [{"sub": "u1", "video_id": ""}, {"sub": "u2", "video_id": "v2"}]
    api = setup(users, admin_emails=["nobody@example.com"])
    api.add_video("v2", "ชื่อเก่า", 42)
    with app.db() as conn:
        rows = {r["sub"]: r for r in conn.execute("SELECT * FROM users")}

    assert app.update_one_user(rows["u1"]) == app.STATUS_NO_VIDEO
    assert app.status_is_error(app.STATUS_NO_VIDEO)
    assert app.quota_used() == 0, "ไม่มี video_id ต้องไม่ยิง API"

    status = app.update_one_user(rows["u2"])
    assert status == "✅ อัปเดตเป็น 42 วิว", status
    assert app.quota_used() == 51
    assert not app.status_is_error(status)
    assert api.update_calls[0]["body"]["snippet"]["description"] == "desc"


def test_owner_lane_ignores_non_owner_rows_and_vice_versa():
    users = [
        {"sub": "owner", "email": "owner@example.com", "video_id": "v1"},
        {"sub": "u2", "email": "u2@example.com", "video_id": "v2"},
    ]
    api = setup(users, admin_emails=["owner@example.com"])
    api.add_video("v1", "views 1", 1)
    api.add_video("v2", "views 2", 2)

    app.run_owner()
    assert [c["ids"] for c in api.list_calls] == [["v1"]]
    app.run_shared()
    assert [c["ids"] for c in api.list_calls] == [["v1"], ["v2"]]


def test_scheduler_starts_two_lanes():
    """เช็คว่าจริง ๆ มีสองงานคนละ interval (ไม่สตาร์ทของจริงพร้อม job ของจริง)"""
    setup([], admin_emails=["nobody@example.com"])
    sched = app.start_scheduler()
    try:
        jobs = {j.id: j for j in sched.get_jobs()}
        assert set(jobs) == {"owner", "shared"}, jobs
        assert jobs["owner"].trigger.interval.total_seconds() / 60 == 10
        assert jobs["shared"].trigger.interval.total_seconds() / 60 == 30
        assert jobs["owner"].func is app.run_owner
        assert jobs["shared"].func is app.run_shared
        # reschedule ของจริงต้องเปลี่ยน interval ได้จริง
        app.shared_job.reschedule(trigger="interval", minutes=123)
        assert app.scheduler.get_job("shared").trigger.interval.total_seconds() / 60 == 123
    finally:
        sched.shutdown(wait=False)
        app.scheduler, app.owner_job, app.shared_job = None, None, None


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - len(failed)}/{len(tests)} ผ่าน")
    if failed:
        print("ตก:", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
