#!/usr/bin/env python3
"""เทสต์ endpoint ที่หน้า admin ใช้ดึงข้อมูลสด

รันด้วย:  ./web/venv/bin/python tests/test_admin_live.py

สำคัญ: ตั้ง RUN_SCHEDULER=0 + ชี้ DATABASE_PATH/QUOTA_STATE_PATH ไปที่ temp
ก่อน import app เสมอ ไม่งั้น import เฉย ๆ จะสตาร์ท scheduler จริงและไปแก้ชื่อ
คลิปจริงของเจ้าของ เทสต์นี้ไม่ยิงเน็ตเลย
"""
import json
import os
import sqlite3
import sys
import tempfile
import traceback

_TMP = tempfile.mkdtemp(prefix="viewtitle-admin-tests-")
os.environ["RUN_SCHEDULER"] = "0"
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "admin.db")
os.environ["QUOTA_STATE_PATH"] = os.path.join(_TMP, "admin-quota.json")
os.environ.setdefault("FLASK_SECRET", "test-secret")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.pop("ADMIN_EMAILS", None)      # ให้ fallback "คนแรกคือแอดมิน" ทำงาน

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))
import app  # noqa: E402

SECRET = "credentials-must-never-leave-the-server"


def seed():
    """คนแรก = เจ้าของ/แอดมิน, คนที่สอง = ผู้ใช้ธรรมดา"""
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.execute("DELETE FROM users")
        conn.execute(
            "INSERT INTO users (sub, email, credentials, title_template, video_id, enabled)"
            " VALUES (?,?,?,?,?,?)",
            ("owner-sub", "owner@example.com", SECRET, "{views} views", "vid123", 1),
        )
        conn.execute(
            "INSERT INTO users (sub, email, credentials, title_template, video_id, enabled)"
            " VALUES (?,?,?,?,?,?)",
            ("other-sub", "other@example.com", SECRET, "{views} views", None, 0),
        )


def client_as(sub=None):
    c = app.app.test_client()
    if sub:
        with c.session_transaction() as s:
            s["sub"] = sub
    return c


def test_anonymous_cannot_read_admin_data():
    assert client_as().get("/admin/data").status_code == 403


def test_non_admin_cannot_read_admin_data():
    assert client_as("other-sub").get("/admin/data").status_code == 403


def test_admin_gets_json():
    r = client_as("owner-sub").get("/admin/data")
    assert r.status_code == 200, r.status_code
    assert r.mimetype == "application/json", r.mimetype


def test_payload_never_carries_credentials_or_sub():
    """ถ้าเทสต์นี้แดง แปลว่า OAuth token ของผู้ใช้กำลังถูกส่งไปให้เบราว์เซอร์"""
    body = client_as("owner-sub").get("/admin/data").get_data(as_text=True)
    assert SECRET not in body, "credentials หลุดออกไปกับ JSON"
    assert "credentials" not in body
    assert "owner-sub" not in body and "other-sub" not in body, "sub ไม่ควรหลุดออกไป"


def test_counts_match_the_table():
    d = json.loads(client_as("owner-sub").get("/admin/data").get_data(as_text=True))
    assert d["stats"] == {"total": 2, "active": 1, "configured": 1}, d["stats"]
    assert len(d["rows"]) == 2
    assert d["quota"]["budget"] == app.QUOTA_BUDGET


def test_only_the_viewer_is_flagged_as_me():
    d = json.loads(client_as("owner-sub").get("/admin/data").get_data(as_text=True))
    mine = [r for r in d["rows"] if r["is_me"]]
    assert len(mine) == 1 and mine[0]["email"] == "owner@example.com", mine


def test_reading_the_panel_costs_no_quota():
    """หน้านี้รีเฟรชทุก 5 วิ ถ้ามันกินโควตาแม้แต่หน่วยเดียวก็ใช้ไม่ได้"""
    before = app.quota_used()
    c = client_as("owner-sub")
    for _ in range(20):
        c.get("/admin/data")
    assert app.quota_used() == before, "การเปิดหน้า admin ไปกินโควตา YouTube"


def test_admin_page_still_renders():
    r = client_as("owner-sub").get("/admin")
    assert r.status_code == 200, r.status_code
    body = r.get_data(as_text=True)
    assert SECRET not in body, "credentials หลุดออกไปกับ HTML"
    assert "owner@example.com" in body


def main():
    seed()
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
