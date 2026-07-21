#!/usr/bin/env python3
"""
เว็บแอป: เปลี่ยนชื่อคลิป YouTube ตามยอดวิว — รองรับหลายผู้ใช้
แต่ละคนล็อกอิน Google ของตัวเอง ใส่ video_id แล้วระบบอัปเดตชื่อให้อัตโนมัติ
"""
import os
import json
import sqlite3
from datetime import datetime

from flask import (
    Flask, redirect, url_for, session, request, render_template, flash, abort
)
import google_auth_oauthlib.flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from apscheduler.schedulers.background import BackgroundScheduler

# Google เติม scope openid ให้เองระหว่างทาง — ผ่อนปรนการเช็ค scope ไม่ให้ error
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
# อนุญาต http เฉพาะตอน dev บน localhost (ตั้ง OAUTHLIB_INSECURE_TRANSPORT=1 ใน .env)

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "data.db"))
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
UPDATE_INTERVAL_MINUTES = int(os.environ.get("UPDATE_INTERVAL_MINUTES", "30"))
DEFAULT_TEMPLATE = "คลิปนี้ของผมมียอดวิว {views} วิว"
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "your-email@example.com")
APP_NAME = os.environ.get("APP_NAME", "ViewTitle")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")


# ---------------------------------------------------------------- ฐานข้อมูล
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                sub            TEXT PRIMARY KEY,
                email          TEXT,
                credentials    TEXT,
                video_id       TEXT,
                title_template TEXT DEFAULT '',
                enabled        INTEGER DEFAULT 0,
                last_status    TEXT DEFAULT '',
                updated_at     TEXT
            )
            """
        )


# ------------------------------------------------------------------- OAuth
def client_config():
    return {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [BASE_URL + "/oauth2callback"],
        }
    }


def make_flow(state=None, code_verifier=None):
    kwargs = dict(
        scopes=SCOPES, state=state, redirect_uri=BASE_URL + "/oauth2callback"
    )
    # PKCE: ตอน login ให้สร้าง verifier ใหม่, ตอน callback ให้ใช้ตัวเดิมจาก session
    if code_verifier is None:
        kwargs["autogenerate_code_verifier"] = True
    else:
        kwargs["code_verifier"] = code_verifier
    return google_auth_oauthlib.flow.Flow.from_client_config(client_config(), **kwargs)


def creds_from_row(row):
    return Credentials.from_authorized_user_info(json.loads(row["credentials"]), SCOPES)


def save_creds(sub, creds):
    with db() as conn:
        conn.execute(
            "UPDATE users SET credentials=? WHERE sub=?", (creds.to_json(), sub)
        )


# ------------------------------------------------------- ตรรกะอัปเดตชื่อคลิป
def build_title(template, views):
    return (template or DEFAULT_TEMPLATE).format(views=f"{views:,}")


def update_one_user(row):
    """คืนค่าข้อความสถานะสั้น ๆ"""
    if not row["video_id"]:
        return "ยังไม่ได้ตั้ง video_id"

    creds = creds_from_row(row)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        save_creds(row["sub"], creds)

    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.videos().list(part="snippet,statistics", id=row["video_id"]).execute()
    items = resp.get("items", [])
    if not items:
        return "ไม่พบคลิป (เป็นเจ้าของคลิปนี้ไหม?)"

    snippet = items[0]["snippet"]
    views = int(items[0]["statistics"].get("viewCount", 0))
    new_title = build_title(row["title_template"], views)

    if snippet.get("title") == new_title:
        return f"วิว {views:,} — ชื่อไม่เปลี่ยน"

    body = {
        "id": row["video_id"],
        "snippet": {
            "title": new_title,
            "categoryId": snippet.get("categoryId", "22"),
            "description": snippet.get("description", ""),
            "tags": snippet.get("tags", []),
        },
    }
    youtube.videos().update(part="snippet", body=body).execute()
    return f"✅ อัปเดตเป็น {views:,} วิว"


def run_all():
    """งานเบื้องหลัง: วนอัปเดตทุกผู้ใช้ที่เปิดใช้งาน"""
    with db() as conn:
        rows = conn.execute("SELECT * FROM users WHERE enabled=1").fetchall()
    for row in rows:
        try:
            status = update_one_user(row)
        except HttpError as e:
            status = f"API error: {e.status_code}"
        except Exception as e:  # noqa: BLE001
            status = f"error: {e}"
        with db() as conn:
            conn.execute(
                "UPDATE users SET last_status=?, updated_at=? WHERE sub=?",
                (status, datetime.now().isoformat(timespec="seconds"), row["sub"]),
            )
    print(f"[{datetime.now():%H:%M:%S}] run_all: อัปเดต {len(rows)} ผู้ใช้", flush=True)


# ------------------------------------------------------------------- Routes
ADMIN_EMAILS = [
    e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
]


def current_user():
    sub = session.get("sub")
    if not sub:
        return None
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE sub=?", (sub,)).fetchone()


def is_admin(user):
    """แอดมิน = อีเมลใน ADMIN_EMAILS; ถ้าไม่ตั้ง ให้ผู้ใช้คนแรก (เจ้าของ) เป็นแอดมิน"""
    if not user:
        return False
    if ADMIN_EMAILS:
        return user["email"] in ADMIN_EMAILS
    with db() as conn:
        first = conn.execute("SELECT sub FROM users ORDER BY rowid LIMIT 1").fetchone()
    return first is not None and first["sub"] == user["sub"]


@app.route("/")
def index():
    return render_template("index.html", user=current_user())


@app.route("/login")
def login():
    flow = make_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["state"] = state
    session["code_verifier"] = flow.code_verifier
    return redirect(auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    flow = make_flow(
        state=session.get("state"), code_verifier=session.get("code_verifier")
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    # ดึงอีเมล/ไอดีผู้ใช้
    userinfo = build("oauth2", "v2", credentials=creds).userinfo().get().execute()
    sub, email = userinfo["id"], userinfo.get("email", "")

    with db() as conn:
        existing = conn.execute("SELECT sub FROM users WHERE sub=?", (sub,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET email=?, credentials=? WHERE sub=?",
                (email, creds.to_json(), sub),
            )
        else:
            conn.execute(
                "INSERT INTO users (sub, email, credentials, title_template) VALUES (?,?,?,?)",
                (sub, email, creds.to_json(), DEFAULT_TEMPLATE),
            )
    session["sub"] = sub
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("index"))
    return render_template(
        "dashboard.html", user=user, interval=UPDATE_INTERVAL_MINUTES,
        is_admin=is_admin(user),
    )


@app.route("/admin")
def admin():
    user = current_user()
    if not is_admin(user):
        abort(403)
    with db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY rowid DESC").fetchall()
    stats = {
        "total": len(rows),
        "active": sum(1 for r in rows if r["enabled"]),
        "configured": sum(1 for r in rows if r["video_id"]),
    }
    return render_template("admin.html", rows=rows, stats=stats, me=user)


@app.route("/save", methods=["POST"])
def save():
    user = current_user()
    if not user:
        abort(403)
    video_id = request.form.get("video_id", "").strip()
    template = request.form.get("title_template", "").strip() or DEFAULT_TEMPLATE
    enabled = 1 if request.form.get("enabled") == "on" else 0
    with db() as conn:
        conn.execute(
            "UPDATE users SET video_id=?, title_template=?, enabled=? WHERE sub=?",
            (video_id, template, enabled, user["sub"]),
        )
    flash("บันทึกแล้ว")
    return redirect(url_for("dashboard"))


@app.route("/run-now", methods=["POST"])
def run_now():
    user = current_user()
    if not user:
        abort(403)
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE sub=?", (user["sub"],)).fetchone()
    try:
        status = update_one_user(row)
    except Exception as e:  # noqa: BLE001
        status = f"error: {e}"
    with db() as conn:
        conn.execute(
            "UPDATE users SET last_status=?, updated_at=? WHERE sub=?",
            (status, datetime.now().isoformat(timespec="seconds"), user["sub"]),
        )
    flash(f"ทดสอบทันที: {status}")
    return redirect(url_for("dashboard"))


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", contact=CONTACT_EMAIL)


@app.route("/terms")
def terms():
    return render_template("terms.html", contact=CONTACT_EMAIL)


@app.route("/delete-account", methods=["POST"])
def delete_account():
    """ลบบัญชี + ถอนสิทธิ์ token (จำเป็นสำหรับ Google verification)"""
    user = current_user()
    if not user:
        abort(403)
    # ถอนสิทธิ์ token ที่ Google แบบ best-effort
    try:
        import urllib.request
        import urllib.parse

        token = json.loads(user["credentials"]).get("token")
        if token:
            urllib.request.urlopen(
                "https://oauth2.googleapis.com/revoke?"
                + urllib.parse.urlencode({"token": token}),
                data=b"",
                timeout=5,
            )
    except Exception:  # noqa: BLE001
        pass
    with db() as conn:
        conn.execute("DELETE FROM users WHERE sub=?", (user["sub"],))
    session.clear()
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# --------------------------------------------------------------- เริ่มระบบ
init_db()

# สตาร์ท scheduler ครั้งเดียว (ต้องรันด้วย gunicorn --workers 1)
if os.environ.get("RUN_SCHEDULER", "1") == "1":
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(run_all, "interval", minutes=UPDATE_INTERVAL_MINUTES)
    scheduler.start()

if __name__ == "__main__":
    # dev เท่านั้น
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
