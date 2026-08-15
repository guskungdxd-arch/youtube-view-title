#!/usr/bin/env python3
"""
เว็บแอป: เปลี่ยนชื่อคลิป YouTube ตามยอดวิว — รองรับหลายผู้ใช้
แต่ละคนล็อกอิน Google ของตัวเอง ใส่ video_id แล้วระบบอัปเดตชื่อให้อัตโนมัติ
"""
import os
import json
import math
import re
import sqlite3
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import (
    Flask, redirect, url_for, session, request, render_template, flash, abort,
    jsonify,
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

# ---- โควตา YouTube Data API + จังหวะการรัน ----------------------------------
# งบจริงของโปรเจกต์คือ 10,000 หน่วย/วัน — ตั้ง default ไว้ 9,000 เผื่อ headroom
QUOTA_BUDGET = int(os.environ.get("QUOTA_BUDGET", "9000"))
# ตัวนับต้องรอดข้าม restart/deploy ไม่งั้นยิงเกินโควตาจริงได้ (ไม่มีระบบ migration
# ในโปรเจกต์นี้ จึงเก็บเป็นไฟล์ JSON ข้าง ๆ ฐานข้อมูล ไม่เพิ่มคอลัมน์/ตาราง)
QUOTA_STATE_PATH = os.environ.get(
    "QUOTA_STATE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "quota_state.json"),
)
# ราคาต่อ call ตามเอกสาร YouTube Data API v3
COST_VIDEOS_LIST = 1
COST_VIDEOS_UPDATE = 50
COST_CHANNELS_LIST = 1
# videos.list รับ id ได้ถึง 50 ตัวต่อครั้ง และคิดเป็น 1 หน่วยเท่าเดิม
VIDEOS_LIST_MAX_IDS = 50

# เจ้าของเว็บได้เลนเร็วคงที่ ส่วนคนอื่นใช้ interval ที่ปรับตามโควตาที่เหลือ
OWNER_INTERVAL_MINUTES = int(os.environ.get("OWNER_INTERVAL_MINUTES", "10"))

# ── โหมดเร่งช่วงเปิดตัวคลิป ───────────────────────────────────────────────
# งบเป็นรายวันแต่ไม่ต้องใช้เท่ากันทุกชั่วโมง ชั่วโมงแรกหลังคลิปขึ้นคือตอนที่มีคนดู
# จริงและยอดขยับเร็วที่สุด จึงยอมทุ่มงบไปตรงนั้นแล้วผ่อนลงทีหลัง
# ตั้งเวลาด้วย env (เวลาไทย): BURST_FROM=2026-08-09T17:00 BURST_UNTIL=2026-08-09T20:00
BURST_FROM = os.environ.get("BURST_FROM", "").strip()
BURST_UNTIL = os.environ.get("BURST_UNTIL", "").strip()
BURST_TZ = os.environ.get("BURST_TZ", "Asia/Bangkok")
# อ่านถี่ได้เพราะอ่าน = 1 หน่วย แต่เขียน = 50 จึงคุมระยะห่างการเขียนแยกต่างหาก
BURST_READ_SECONDS = int(os.environ.get("BURST_READ_SECONDS", "30"))
BURST_MIN_WRITE_SECONDS = int(os.environ.get("BURST_MIN_WRITE_SECONDS", "120"))
# กันโหมดเร่งดูดงบจนหมดเกลี้ยง — ต่ำกว่านี้แล้วถอยกลับไปจังหวะปกติ
BURST_RESERVE_UNITS = int(os.environ.get("BURST_RESERVE_UNITS", "600"))
MIN_INTERVAL_MINUTES = int(
    os.environ.get("MIN_INTERVAL_MINUTES", str(UPDATE_INTERVAL_MINUTES))
)
MAX_INTERVAL_MINUTES = int(os.environ.get("MAX_INTERVAL_MINUTES", "360"))

# โควตา YouTube รีเซ็ตเที่ยงคืน "เวลาแปซิฟิก" ไม่ใช่ UTC และไม่ใช่เวลาเครื่อง
PACIFIC = ZoneInfo("America/Los_Angeles")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "your-email@example.com")
ADMIN_EMAILS = [
    e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
]
APP_NAME = os.environ.get("APP_NAME", "ViewTitle")

# โปรเจกต์ที่โชว์บนหน้าแรก (portfolio hub) — "logo" คือชื่อไฟล์ใน web/static/
PROJECTS = [
    {
        "name": "ViewTitle",
        "tagline": "Your video title, always up to date.",
        "description": "Automatically rewrites a YouTube video's title with its live view count, so the title always shows the real number.",
        "url": "/viewtitle",
        "status": "Live",
        "tags": ["Flask", "YouTube Data API", "OAuth 2.0"],
        "logo": "logo.png",
    },
    {
        "name": "Channel Stats",
        "tagline": "Subscribers and views, live from the API.",
        "description": "A scoreboard for my own YouTube channel — subscriber count, total views and video count, pulled straight from the YouTube Data API and cached.",
        "url": "/channel",
        "status": "Live",
        "tags": ["Flask", "YouTube Data API", "Caching"],
        # ไอคอนคนแบบ pixel — สร้างด้วย tools/make_avatar_logo.py
        "logo": "logo-channel.png",
    },
    {
        "name": "Flow",
        "tagline": "How the other two actually work.",
        "description": "Animated diagrams of the systems on this site — the OAuth handshake, the update loop and what it costs in API quota, and the cache that keeps a public page free.",
        "url": "/flow",
        "status": "Live",
        "tags": ["CSS animation", "Systems design", "No JS"],
        # ไอคอนท่อส่งข้อมูลแบบ pixel — สร้างด้วย tools/make_flow_logo.py
        "logo": "logo-flow.png",
    },
    {
        "name": "Snake",
        "tagline": "One line of code per subscriber.",
        "description": "A snake game written under a hard budget: the playable game may use only as many lines as the channel has subscribers. The leaderboard around it is scored server-side, so a score has to be earned.",
        "url": "/snake",
        "status": "Live",
        "tags": ["Canvas", "Game loop", "SQLite"],
        # ไอคอนงูแบบ pixel — สร้างด้วย tools/make_snake_logo.py
        "logo": "logo-snake.png",
    },
]

# กติกาของชาเลนจ์: โค้ดตัวเกมยาวได้เท่าจำนวนซับ ตัวเลขนี้โชว์บนหน้า /snake
SNAKE_LINE_BUDGET = int(os.environ.get("SNAKE_LINE_BUDGET", "160"))

# กระดาน 20x20 = 400 ช่อง งูเริ่มยาว 4 → กินได้มากสุด 396 ครั้ง ครั้งละ 10 คะแนน
SNAKE_MAX_SCORE = 3960
SNAKE_COOLDOWN_SECONDS = 2

SNAKE_GRID = 20
SNAKE_BOARD = 400                      # ขนาด canvas เป็นพิกเซล
SNAKE_CELLS = (SNAKE_BOARD // SNAKE_GRID) ** 2   # 400 ช่อง
SNAKE_MAX_STEPS = 100_000              # กันส่ง steps มหาศาลมาถ่วงเซิร์ฟเวอร์
SNAKE_MAX_INPUTS = 20_000
SNAKE_RUN_TTL = 6 * 3600               # seed ที่ไม่ถูกใช้ ล้างทิ้งหลัง 6 ชม.
# ต่ำสุดที่เกมเดินได้จริงต่อ 1 step — เกมขยับทุก 6 เฟรม จอ 240Hz ก็ยังได้ ~25ms
# ใครอ้างว่าเล่นพันสเต็ปในสามวินาที คือปลอม
SNAKE_MIN_MS_PER_STEP = 20

# ปุ่มลูกศร → ทิศ ต้องตรงกับ e.which ใน snake.html
SNAKE_KEYS = {
    37: (-SNAKE_GRID, 0),
    38: (0, -SNAKE_GRID),
    39: (SNAKE_GRID, 0),
    40: (0, SNAKE_GRID),
}

# ไดอะแกรมบนหน้า /flow — เมนูอยู่ที่ /flow, ตัวไดอะแกรมอยู่ที่ /flow/<slug>
# เพิ่มไดอะแกรมใหม่ = append dict ที่นี่ + เพิ่มบล็อกในเทมเพลตที่ตรงกับ slug
FLOWS = [
    {
        "slug": "viewtitle-auth",
        "project": "ViewTitle",
        "name": "Signing in",
        "blurb": "OAuth 2.0 with PKCE — why the request that starts the login and the "
                 "request that finishes it have to prove they are the same request.",
        "caption": "Same verifier, or no token",
    },
    {
        "slug": "viewtitle-update",
        "project": "ViewTitle",
        "name": "The update cycle",
        "blurb": "What the background job does every cycle, and why the whole design is "
                 "shaped by a read costing 1 quota unit and a write costing 50.",
        "caption": "Reads are cheap — writes cost 50",
    },
    {
        "slug": "channel-cache",
        "project": "Channel Stats",
        "name": "The cache",
        "blurb": "How a public page serves every visitor from one API call, so the quota "
                 "bill stays flat no matter how much traffic it gets.",
        "caption": "One read serves every visitor",
    },
]

FLOWS_BY_SLUG = {f["slug"]: f for f in FLOWS}

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
        # กระดานคะแนนเกมงู — 1 แถวต่อ 1 ผู้ใช้ เก็บเฉพาะคะแนนดีที่สุด
        # ไม่ผูก FOREIGN KEY กับ users เพราะลบบัญชีแล้วยังอยากให้คะแนนคงอยู่
        #
        # แยกเวลาสองตัวโดยตั้งใจ:
        #   best_at    = ตอนที่ทำคะแนนสูงสุดนี้ได้ ไม่ขยับถ้าส่งคะแนนที่ต่ำกว่ามา
        #                ใช้ตัดสิน "ใครเก็บเต็มกระดานได้ก่อน" จึงห้ามถูกเขียนทับ
        #   updated_at = ตอนส่งครั้งล่าสุด ใช้ทำ cooldown อย่างเดียว
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snake_scores (
                sub        TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                score      INTEGER NOT NULL,
                best_at    REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS snake_scores_score_idx"
            " ON snake_scores (score DESC)"
        )
        # seed ที่แจกไปแล้วรอผู้เล่นส่งผลกลับมา หนึ่งใบต่อหนึ่งเกม ใช้ซ้ำไม่ได้
        # ถ้าไม่มีตารางนี้ ใครอัดคลิปการเล่นที่ชนะไว้ครั้งเดียว จะส่งซ้ำได้ไม่จำกัด
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snake_runs (
                game_id   TEXT PRIMARY KEY,
                sub       TEXT NOT NULL,
                seed      INTEGER NOT NULL,
                issued_at REAL NOT NULL
            )
            """
        )


# ------------------------------------------------------------- นับโควตา API
# วัดจากของจริง: บวกที่จุดเรียก API เท่านั้น (yt_videos_list / yt_videos_update)
# ตัวเลขจึงไม่มีทางเพี้ยนจากที่ยิงไปจริง
_quota_lock = threading.RLock()
_quota_cache = None  # dict สถานะของ "วันนี้" ตามเวลาแปซิฟิก
_quota_write_failed = False


def _now_pacific():
    """เวลาปัจจุบันโซนแปซิฟิก — แยกเป็นฟังก์ชันเพื่อให้เทสต์แทนนาฬิกาได้"""
    return datetime.now(PACIFIC)


def _pacific_date(now=None):
    return (now or _now_pacific()).astimezone(PACIFIC).strftime("%Y-%m-%d")


def minutes_until_quota_reset(now=None):
    """เหลืออีกกี่นาทีถึงเที่ยงคืนแปซิฟิก (จุดที่โควตารีเซ็ต)"""
    now = (now or _now_pacific()).astimezone(PACIFIC)
    nxt = now + timedelta(days=1)
    reset = datetime(nxt.year, nxt.month, nxt.day, tzinfo=PACIFIC)
    return max((reset - now).total_seconds() / 60.0, 1.0)


def _blank_quota_state(now=None):
    return {
        "date": _pacific_date(now),
        "used": 0,
        "last_shared_cost": 0,
        "shared_interval": MIN_INTERVAL_MINUTES,
    }


def _read_quota_file():
    try:
        with open(QUOTA_STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("date"), str):
        return None
    state = _blank_quota_state()
    state["date"] = data["date"]
    for key in ("used", "last_shared_cost", "shared_interval"):
        try:
            state[key] = int(data.get(key, state[key]))
        except (TypeError, ValueError):
            pass
    return state


def _write_quota_file(state):
    """เขียนแบบ atomic (tmp + replace) ไฟล์พังกลางทางแล้วอ่านไม่ออกจะแย่กว่า"""
    global _quota_write_failed
    tmp = QUOTA_STATE_PATH + ".tmp"
    try:
        directory = os.path.dirname(QUOTA_STATE_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, QUOTA_STATE_PATH)
        _quota_write_failed = False
    except OSError as e:
        if not _quota_write_failed:  # เตือนครั้งเดียว ไม่สแปม log
            print(f"[quota] เขียน {QUOTA_STATE_PATH} ไม่ได้: {e}", flush=True)
            _quota_write_failed = True


def quota_state(now=None):
    """สถานะโควตาของวันนี้ (เวลาแปซิฟิก) — ข้ามวันแล้วรีเซ็ตให้เอง"""
    global _quota_cache
    with _quota_lock:
        today = _pacific_date(now)
        state = _quota_cache if _quota_cache is not None else _read_quota_file()
        if state is None or state.get("date") != today:
            state = _blank_quota_state(now)
            _write_quota_file(state)
        _quota_cache = state
        return state


def quota_charge(units, what=""):
    """บันทึกว่าใช้โควตาไปแล้ว units หน่วย — เรียกที่จุดยิง API เท่านั้น"""
    with _quota_lock:
        state = quota_state()
        state["used"] = int(state.get("used", 0)) + int(units)
        _write_quota_file(state)
        return state["used"]


def quota_used(now=None):
    return int(quota_state(now).get("used", 0))


def quota_remaining(now=None):
    return QUOTA_BUDGET - quota_used(now)


def shared_interval_minutes():
    """interval ที่ใช้กับผู้ใช้ทั่วไปตอนนี้ (clamp เผื่อค่าใน state เก่า/env เปลี่ยน)"""
    value = int(quota_state().get("shared_interval", MIN_INTERVAL_MINUTES))
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, value))


def compute_shared_interval(last_run_cost, now=None):
    """กระจายโควตาที่เหลือให้ครบถึงตอนรีเซ็ต โดยอ้างจากราคาที่รอบก่อนใช้จริง"""
    remaining_units = max(quota_remaining(now), 0)
    remaining_minutes = minutes_until_quota_reset(now)
    if last_run_cost <= 0:
        ideal = 0.0
    else:
        ideal = remaining_minutes * last_run_cost / max(remaining_units, 1)
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, math.ceil(ideal)))


def read_batch_count(n_videos):
    """videos.list กี่ครั้งสำหรับคลิป n ตัว (= กี่หน่วยโควตาสำหรับการอ่าน)"""
    return math.ceil(n_videos / VIDEOS_LIST_MAX_IDS) if n_videos else 0


def estimated_shared_run_cost(n_videos, last_run_cost):
    """ราคาที่คาดว่ารอบถัดไปจะใช้ — ใช้ตัดสินว่า 'เหลือพอรันอีกรอบไหม'"""
    reads = read_batch_count(n_videos)
    if last_run_cost and last_run_cost > 0:
        return max(int(last_run_cost), reads)
    # ยังไม่เคยรัน: เผื่อไว้ว่ามีคลิปเปลี่ยนชื่ออย่างน้อยหนึ่งคลิป
    return reads + (COST_VIDEOS_UPDATE if n_videos else 0)


# ---- ตัวห่อ API: ทุกการยิงต้องผ่านสองฟังก์ชันนี้ เพื่อให้ตัวนับตรงกับความจริง ----
def yt_videos_list(youtube, video_ids):
    """อ่านได้ถึง 50 id ต่อครั้ง = 1 หน่วย (คิดเงินก่อนยิง เพราะยิงแล้ว Google นับ)"""
    quota_charge(COST_VIDEOS_LIST, "videos.list")
    return (
        youtube.videos()
        .list(part="snippet,statistics", id=",".join(video_ids))
        .execute()
    )


def yt_videos_update(youtube, body):
    """เขียนทีละคลิปเท่านั้น (batch ไม่ได้) = 50 หน่วยต่อครั้ง"""
    quota_charge(COST_VIDEOS_UPDATE, "videos.update")
    return youtube.videos().update(part="snippet", body=body).execute()


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


def _youtube_for_row(row):
    """client ของผู้ใช้แถวนี้ — refresh + เก็บ token ใหม่ลง DB ถ้าหมดอายุ

    build() ใช้ discovery doc ที่ฝังมากับ library ไม่ยิงเน็ตและไม่กินโควตา
    """
    creds = creds_from_row(row)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        save_creds(row["sub"], creds)
    return build("youtube", "v3", credentials=creds)


# ------------------------------------------------- สถิติช่อง (หน้า /channel)
# หน้านี้เป็นหน้าสาธารณะ ถ้ายิง API ทุก request คนเข้าเยอะจะกิน quota ทันที
# จึง cache ไว้ CHANNEL_CACHE_TTL วินาที — ที่ค่า default จะยิงมากสุด 48 ครั้ง/วัน (48 หน่วย)
CHANNEL_CACHE_TTL = int(os.environ.get("CHANNEL_CACHE_TTL", "1800"))
_channel_cache = {"at": 0.0, "data": None}

# เป้ายอด subscriber ที่โชว์บนแถบ progress ในหน้า /channel
CHANNEL_SUB_GOAL = int(os.environ.get("CHANNEL_SUB_GOAL", "500"))
# บันไดหมุดหมาย ถ้ายอดจริงแตะเป้าแล้วให้เลื่อนขึ้นขั้นถัดไป แถบจะได้ไม่เต็ม/ล้น
_GOAL_LADDER = [500, 1000, 2500, 5000, 10000, 25000, 50000, 100000]


def channel_sub_goal(subs):
    """เป้า sub ที่จะโชว์ ถ้ายอดถึงเป้าแล้วเลื่อนขึ้นหมุดถัดไปในบันได"""
    goal = CHANNEL_SUB_GOAL
    if not subs:
        return goal
    while subs >= goal:
        nxt = next((m for m in _GOAL_LADDER if m > goal), None)
        goal = nxt if nxt is not None else goal * 2
    return goal


def _owner_row():
    """แถวของเจ้าของเว็บ = ผู้ใช้คนแรกที่ล็อกอิน (ตรรกะเดียวกับ is_admin() fallback)"""
    with db() as conn:
        return conn.execute("SELECT * FROM users ORDER BY rowid LIMIT 1").fetchone()


def fetch_channel_stats():
    """ดึงสถิติช่องของเจ้าของ ผ่าน token ที่เก็บไว้แล้ว (scope force-ssl ครอบคลุมอยู่)"""
    row = _owner_row()
    if row is None or not row["credentials"]:
        return None

    youtube = _youtube_for_row(row)
    # หน้านี้ก็กินโควตาก้อนเดียวกับ scheduler (channels.list = 1 หน่วย) จึงต้องนับด้วย
    quota_charge(COST_CHANNELS_LIST, "channels.list")
    resp = youtube.channels().list(part="snippet,statistics", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        return None

    snip = items[0]["snippet"]
    st = items[0]["statistics"]
    thumbs = snip.get("thumbnails", {})
    thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url")
    return {
        "title": snip.get("title", ""),
        "thumb": thumb,
        # YouTube ซ่อนยอด sub ได้ ถ้าซ่อนจะไม่ส่ง subscriberCount มา
        "subs_hidden": st.get("hiddenSubscriberCount", False),
        "subs": int(st.get("subscriberCount", 0)),
        "views": int(st.get("viewCount", 0)),
        "videos": int(st.get("videoCount", 0)),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def get_channel_stats():
    """คืนสถิติจาก cache; ถ้าดึงใหม่ล้มเหลวให้คืนค่าเก่าไว้ ดีกว่าโชว์หน้าพัง"""
    now = time.monotonic()
    cached = _channel_cache["data"]
    if cached is not None and now - _channel_cache["at"] < CHANNEL_CACHE_TTL:
        return cached
    try:
        data = fetch_channel_stats()
    except Exception as e:  # noqa: BLE001
        print(f"[channel] ดึงสถิติไม่สำเร็จ: {e}", flush=True)
        return cached
    if data is not None:
        _channel_cache["at"] = now
        _channel_cache["data"] = data
        return data
    return cached


# ------------------------------------------------------- ตรรกะอัปเดตชื่อคลิป
# ข้อความสถานะที่นับเป็น "ล้มเหลว" — เก็บเป็นค่าคงที่เพราะ status_is_error() ต้องเทียบตรง ๆ
STATUS_NO_VIDEO = "ยังไม่ได้ตั้ง video_id"
STATUS_NOT_FOUND = "ไม่พบคลิป (เป็นเจ้าของคลิปนี้ไหม?)"


def build_title(template, views):
    return (template or DEFAULT_TEMPLATE).format(views=f"{views:,}")


def status_is_error(status):
    """สถานะนี้เป็นความล้มเหลวไหม — ใช้ตัดสินว่า flash ควรเป็นสีแดง

    เทียบกับค่าคงที่ที่ update_one_user คืนจริง ๆ ไม่ใช่เดาจากข้อความ
    เพื่อไม่ให้หลุดกันเวลาแก้ถ้อยคำ
    """
    return status.startswith("error:") or status in (STATUS_NO_VIDEO, STATUS_NOT_FOUND)


# เวลาที่เขียนชื่อสำเร็จครั้งล่าสุดของแต่ละคน — เก็บในหน่วยความจำพอ ถ้า restart แล้วหาย
# ผลเสียคือเขียนเกินมาหนึ่งครั้ง (50 หน่วย) ไม่คุ้มกับการเพิ่มคอลัมน์ในฐานข้อมูล
_last_write = {}
_last_write_lock = threading.RLock()


def write_cooldown_left(sub, now=None):
    """เหลืออีกกี่วินาทีถึงจะเขียนชื่อคนนี้ได้อีกครั้ง (0 = เขียนได้เลย)

    บังคับเฉพาะตอนโหมดเร่ง — นอกโหมดนี้เลนเจ้าของวิ่งทุก 10 นาทีอยู่แล้ว
    ระยะห่างจึงมาจากตัว scheduler เอง ไม่ต้องมีตัวคุมซ้อน
    """
    if not burst_active(now) or BURST_MIN_WRITE_SECONDS <= 0:
        return 0
    with _last_write_lock:
        last = _last_write.get(sub)
    if last is None:
        return 0
    gap = (now or datetime.now()).timestamp() - last
    return max(0, math.ceil(BURST_MIN_WRITE_SECONDS - gap))


def _mark_written(sub, now=None):
    with _last_write_lock:
        _last_write[sub] = (now or datetime.now()).timestamp()


def _apply_item(row, item, youtube):
    """ตัดสินใจจาก item ที่อ่านมาแล้วว่าต้องเปลี่ยนชื่อไหม แล้วคืนข้อความสถานะ

    item ต้องเป็นของคลิปแถวนี้เท่านั้น — snippet ที่ส่งกลับไปคือของคลิปตัวเอง
    เพราะ videos.update ทับ snippet ทั้งก้อน ถ้าส่งแค่ title คำบรรยาย/แท็ก/หมวดหาย
    """
    snippet = item.get("snippet", {})
    views = int(item.get("statistics", {}).get("viewCount", 0))
    new_title = build_title(row["title_template"], views)

    # ชื่อเดิมตรงอยู่แล้ว = ไม่ต้องจ่าย 50 หน่วย (หัวใจของการประหยัดโควตา)
    if snippet.get("title") == new_title:
        return f"วิว {views:,} — ชื่อไม่เปลี่ยน"

    # ตอนเร่ง เราอ่านทุก 30 วิ (1 หน่วย) แต่ไม่ได้แปลว่าจะเขียนได้ทุก 30 วิ (50 หน่วย)
    # ถ้ายอดขยับถี่กว่าระยะนี้ก็ปล่อยให้มันสะสมแล้วเขียนทีเดียว — ชื่อยังเป็นเลขล่าสุด
    wait = write_cooldown_left(row["sub"])
    if wait:
        return f"วิว {views:,} — รออีก {wait} วิ ค่อยเขียน"

    body = {
        "id": row["video_id"],
        "snippet": {
            "title": new_title,
            "categoryId": snippet.get("categoryId", "22"),
            "description": snippet.get("description", ""),
            "tags": snippet.get("tags", []),
        },
    }
    yt_videos_update(youtube, body)
    _mark_written(row["sub"])
    return f"✅ อัปเดตเป็น {views:,} วิว"


def update_one_user(row, item=None):
    """คืนค่าข้อความสถานะสั้น ๆ

    item = ผลอ่านที่ prefetch มาแล้วจาก batch (ถ้ามี) — ถ้าไม่ส่งมาจะอ่านเอง
    การ update ใช้ credentials ของเจ้าตัวเสมอ ไม่ว่าใครเป็นคนอ่าน
    """
    if not row["video_id"]:
        return STATUS_NO_VIDEO

    youtube = _youtube_for_row(row)
    if item is None:
        resp = yt_videos_list(youtube, [row["video_id"]])
        # จับคู่ด้วย id เป๊ะ ๆ ไม่ใช่ items[0] — กัน video_id เพี้ยนพา snippet ผิดคลิป
        # มาเขียนทับ (และไม่ต้องเสีย 50 หน่วยไปกับ update ที่ยังไงก็ 404)
        item = next(
            (i for i in resp.get("items", []) if i.get("id") == row["video_id"]), None
        )
        if item is None:
            return STATUS_NOT_FOUND
    return _apply_item(row, item, youtube)


# ----------------------------------------------- อ่านแบบ batch (ประหยัดโควตา
# id ที่หน้าตาไม่ใช่ video id (มี comma/ช่องว่าง) ห้ามเอาไปรวมก้อน ไม่งั้นคนเดียว
# ใส่ค่าเพี้ยนแล้วทำให้ก้อนของคนอื่นพังหรือกินโควตาเพิ่มได้ — ปล่อยให้อ่านรายคนไป
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _batch_read(rows):
    """อ่านคลิปของ rows ทั้งชุดด้วย videos.list ก้อนละ ≤50 id (1 หน่วยต่อก้อน)

    ความถูกต้อง: videos.list?id=... เป็นการอ่าน "สาธารณะ" ใครถือ token ที่ใช้ได้
    ก็อ่านคลิป public ของคนอื่นได้ แต่คลิป private/unlisted จะไม่ถูกส่งกลับมา
    ถ้าไม่ได้ใช้ token ของเจ้าของคลิป จึงถือว่า id ที่ "ไม่กลับมา" = ยังไม่รู้ผล
    แล้วปล่อยให้ผู้เรียกไปอ่านซ้ำด้วย credentials ของเจ้าตัว (1 หน่วย) ไม่เดาแทน
    """
    found = {}
    ids, rows_by_id = [], {}
    for row in rows:
        vid = row["video_id"]
        if not vid or not _VIDEO_ID_RE.match(vid):
            continue
        if vid not in rows_by_id:
            ids.append(vid)
            rows_by_id[vid] = []
        rows_by_id[vid].append(row)

    for start in range(0, len(ids), VIDEOS_LIST_MAX_IDS):
        batch = ids[start : start + VIDEOS_LIST_MAX_IDS]
        # ผู้อ่านที่เป็นไปได้ = เจ้าของคลิปในก้อนนี้ ลองไม่เกิน 2 คน
        # ถ้า token คนแรกใช้ไม่ได้ก็อย่าไล่ยิงทั้งก้อนจนโควตาหมด
        candidates = [rows_by_id[vid][0] for vid in batch][:2]
        for reader in candidates:
            try:
                resp = yt_videos_list(_youtube_for_row(reader), batch)
            except Exception as e:  # noqa: BLE001
                print(f"[batch] อ่านก้อน {len(batch)} id ไม่สำเร็จ: {e}", flush=True)
                continue
            for item in resp.get("items", []):
                if item.get("id") in rows_by_id:
                    found[item["id"]] = item
            break
    return found


def _save_status(sub, status):
    with db() as conn:
        conn.execute(
            "UPDATE users SET last_status=?, updated_at=? WHERE sub=?",
            (status, datetime.now().isoformat(timespec="seconds"), sub),
        )


def _process_rows(rows):
    """อ่าน batch แล้วอัปเดตทีละคน — คืนจำนวนหน่วยโควตาที่ใช้ไปจริง (วัดจากตัวนับ)"""
    before = quota_used()
    prefetched = {}
    if rows:
        try:
            prefetched = _batch_read(rows)
        except Exception as e:  # noqa: BLE001
            print(f"[batch] ข้ามการอ่านรวม: {e}", flush=True)
    for row in rows:
        try:
            # ไม่อยู่ใน prefetched = อ่านรวมไม่เห็น (คลิปส่วนตัว/ไม่มีจริง) → อ่านซ้ำเอง
            status = update_one_user(row, item=prefetched.get(row["video_id"]))
        except HttpError as e:
            status = f"API error: {e.status_code}"
        except Exception as e:  # noqa: BLE001
            status = f"error: {e}"
        _save_status(row["sub"], status)
    # ถ้าโควตารีเซ็ตคาบเกี่ยวกลางรอบ ตัวนับจะย้อนกลับ — อย่าคืนค่าติดลบ
    return max(0, quota_used() - before)


# ------------------------------------------------- ใครเป็นเจ้าของ / ใครกินโควตา
def owner_subs():
    """sub ของเจ้าของ/แอดมิน — ตรรกะเดียวกับ is_admin() เป๊ะ ๆ"""
    with db() as conn:
        if ADMIN_EMAILS:
            rows = conn.execute("SELECT sub, email FROM users").fetchall()
            return {r["sub"] for r in rows if r["email"] in ADMIN_EMAILS}
        first = conn.execute("SELECT sub FROM users ORDER BY rowid LIMIT 1").fetchone()
        return {first["sub"]} if first else set()


def quota_rows():
    """เฉพาะผู้ใช้ที่ทำให้เกิดการยิง API จริง

    คนที่สมัครแล้วยังไม่ตั้ง video_id ไม่กินโควตา จึงต้องไม่ถูกนับ
    ไม่งั้นสูตรคิด interval จะยืดเวลาให้คนอื่นฟรี ๆ
    """
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users "
            "WHERE enabled=1 AND video_id IS NOT NULL AND video_id != ''"
        ).fetchall()


def _split_lanes():
    owners = owner_subs()
    rows = quota_rows()
    return (
        [r for r in rows if r["sub"] in owners],
        [r for r in rows if r["sub"] not in owners],
    )


# ------------------------------------------------------------ งานเบื้องหลัง
def run_owner():
    """เลนเจ้าของ: รันถี่คงที่ และ **ไม่** เช็คงบที่แชร์กับคนอื่น

    เจ้าของต้องได้รันแม้งบส่วนรวมจะหมด — นั่นคือความหมายของการให้ priority
    """
    owner_rows, _ = _split_lanes()
    spent = _process_rows(owner_rows)
    # ตรวจหลังรัน ไม่ใช่ก่อน — หน้าต่างเวลาอาจเพิ่งเปิด/ปิด หรืองบเพิ่งตกใต้ reserve
    # จากรอบนี้เอง จังหวะถัดไปต้องสะท้อนสถานะล่าสุดเสมอ
    seconds = _reschedule_owner(owner_interval_seconds())
    print(
        f"[{datetime.now():%H:%M:%S}] run_owner: {len(owner_rows)} คน "
        f"ใช้ {spent} หน่วย (ใช้ไปวันนี้ {quota_used()}/{QUOTA_BUDGET}) "
        f"รอบถัดไปอีก {seconds} วิ{' [เร่ง]' if burst_active() else ''}",
        flush=True,
    )
    return spent


def run_shared():
    """เลนผู้ใช้ทั่วไป: ใช้งบที่เหลือ และปรับ interval ตามงบ + เวลาที่เหลือถึงรีเซ็ต"""
    _, other_rows = _split_lanes()
    now = _now_pacific()
    state = quota_state(now)
    estimate = estimated_shared_run_cost(len(other_rows), state.get("last_shared_cost"))

    if other_rows and quota_remaining(now) < estimate:
        # ไม่พอรันอีกรอบ → ข้ามไปจนโควตารีเซ็ต (เลนเจ้าของไม่โดนหยุดด้วย)
        wait = _wait_for_reset_minutes(now)
        print(
            f"[{datetime.now():%H:%M:%S}] run_shared: ข้าม — เหลือ "
            f"{quota_remaining(now)} หน่วย ต้องใช้ ~{estimate} "
            f"รอโควตารีเซ็ตอีก {wait} นาที",
            flush=True,
        )
        _reschedule_shared(wait, persist=False)
        return 0

    spent = _process_rows(other_rows)
    with _quota_lock:
        state = quota_state()
        state["last_shared_cost"] = spent
        _write_quota_file(state)
    interval = compute_shared_interval(spent, _now_pacific())
    _reschedule_shared(interval)
    print(
        f"[{datetime.now():%H:%M:%S}] run_shared: {len(other_rows)} คน "
        f"ใช้ {spent} หน่วย (ใช้ไปวันนี้ {quota_used()}/{QUOTA_BUDGET}) "
        f"รอบถัดไปอีก {interval} นาที",
        flush=True,
    )
    return spent


def run_all():
    """รันทั้งสองเลนติดกัน — เผื่อเรียกมือ (scheduler ใช้ run_owner/run_shared แยกกัน)"""
    return run_owner() + run_shared()


# ------------------------------------------------------ โหมดเร่งช่วงเปิดตัว
def _burst_bound(value):
    """แปลงค่า env เป็น datetime มี tz; ค่าว่างหรือพังคือ 'ไม่ตั้ง' ไม่ใช่ error

    ถ้าเขียนวันที่ผิดแล้วแอปตายตอน import คือทำเว็บล่มเพราะพิมพ์ผิด ไม่คุ้ม
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        print(f"[burst] อ่านเวลา {value!r} ไม่ออก — ถือว่าไม่ได้ตั้งโหมดเร่ง", flush=True)
        return None
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(BURST_TZ))
        except Exception:  # noqa: BLE001
            print(f"[burst] ไม่รู้จักโซนเวลา {BURST_TZ!r} — ใช้เวลาเครื่อง", flush=True)
            parsed = parsed.astimezone()
    return parsed


def burst_window():
    return _burst_bound(BURST_FROM), _burst_bound(BURST_UNTIL)


def burst_active(now=None):
    """อยู่ในหน้าต่างเวลาที่ตั้งไว้ **และ** งบยังเหลือมากกว่า reserve

    เงื่อนไขงบสำคัญพอ ๆ กับเงื่อนไขเวลา: เลนเจ้าของไม่เช็คงบโดยตั้งใจ ถ้าปล่อยให้
    อ่านทุก 30 วิ + เขียนทุก 2 นาทีจนงบหมดเกลี้ยง เลนรวมของคนอื่นจะไม่เหลืออะไรเลย
    """
    start, end = burst_window()
    if start is None or end is None:
        return False
    now = now or datetime.now(ZoneInfo(BURST_TZ) if BURST_TZ else None).astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    if not (start <= now < end):
        return False
    return quota_remaining() > BURST_RESERVE_UNITS


def owner_interval_seconds(now=None):
    return BURST_READ_SECONDS if burst_active(now) else OWNER_INTERVAL_MINUTES * 60


# ------------------------------------------------------- คุม scheduler / จังหวะ
# APScheduler ตั้ง interval ครั้งเดียวตอน add_job — ถ้าอยากให้เปลี่ยนจริงต้อง
# reschedule() ทุกรอบ ไม่ใช่แค่เปลี่ยนค่าตัวแปร
scheduler = None
owner_job = None
shared_job = None


def _wait_for_reset_minutes(now=None):
    """ตอนข้ามรอบเพราะโควตาหมด ให้ตื่นอีกทีหลังโควตารีเซ็ต (การเช็คไม่กินโควตา)"""
    return max(5, min(MAX_INTERVAL_MINUTES, math.ceil(minutes_until_quota_reset(now)) + 1))


def _reschedule_owner(seconds):
    """เลนเจ้าของสลับจังหวะเองระหว่างโหมดเร่งกับปกติ"""
    seconds = max(10, int(seconds))
    if owner_job is None:
        return seconds
    try:
        owner_job.reschedule(trigger="interval", seconds=seconds)
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] reschedule เลนเจ้าของไม่สำเร็จ: {e}", flush=True)
    return seconds


def _reschedule_shared(minutes, persist=True):
    minutes = max(1, int(minutes))
    if persist:
        with _quota_lock:
            state = quota_state()
            state["shared_interval"] = minutes
            _write_quota_file(state)
    if shared_job is None:
        return minutes
    try:
        shared_job.reschedule(trigger="interval", minutes=minutes)
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] reschedule ไม่สำเร็จ: {e}", flush=True)
    return minutes


def start_scheduler():
    """เรียกครั้งเดียวตอน import และต้องรันด้วย gunicorn --workers 1"""
    global scheduler, owner_job, shared_job
    scheduler = BackgroundScheduler(daemon=True)
    owner_job = scheduler.add_job(
        run_owner, "interval", seconds=owner_interval_seconds(),
        id="owner", max_instances=1, coalesce=True,
    )
    shared_job = scheduler.add_job(
        run_shared, "interval", minutes=shared_interval_minutes(),
        id="shared", max_instances=1, coalesce=True,
    )
    scheduler.start()
    start, end = burst_window()
    window = f", โหมดเร่ง {start:%d/%m %H:%M}–{end:%H:%M}" if start and end else ""
    print(
        f"[scheduler] เลนเจ้าของทุก {owner_interval_seconds()} วิ, "
        f"เลนรวมเริ่มที่ {shared_interval_minutes()} นาที "
        f"(งบ {QUOTA_BUDGET} หน่วย/วัน, ใช้ไปแล้ว {quota_used()}){window}",
        flush=True,
    )
    return scheduler


def human_interval(seconds):
    """'30 s' / '10 min' — จังหวะเลนเจ้าของสลับหน่วยได้ ป้ายบนหน้าเว็บจึงต้องสลับตาม"""
    seconds = int(seconds)
    return f"{seconds} s" if seconds < 60 else f"{round(seconds / 60)} min"


def interval_for_user(user):
    """interval ที่ใช้กับผู้ใช้คนนี้จริง ๆ — เอาไปโชว์บน dashboard ให้ตรงความจริง"""
    if user and user["sub"] in owner_subs():
        return human_interval(owner_interval_seconds()), False
    return f"{shared_interval_minutes()} min", True


# ------------------------------------------------------------------- Routes
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


@app.context_processor
def inject_base_url():
    """ให้ทุก template ประกอบ absolute URL ได้ — og:image ต้องเป็น absolute เสมอ

    ไม่ใช้ url_for(_external=True) เพราะแอปอยู่หลัง nginx และไม่ได้ตั้ง ProxyFix
    มันจึงอาจได้ scheme เป็น http แทน https
    """
    return {"base_url": BASE_URL}


@app.route("/")
def home():
    """หน้าแรก = portfolio hub รวมโปรเจกต์"""
    return render_template("home.html", projects=PROJECTS, user=current_user())


@app.route("/viewtitle")
def viewtitle():
    """หน้า landing เดิมของ ViewTitle (Google review อ่านหน้านี้)"""
    return render_template("viewtitle.html", user=current_user())


@app.route("/flow")
def flow():
    """เมนูเลือกไดอะแกรม — หน้า static ล้วน ไม่ยิง API"""
    return render_template(
        "flow.html", flows=FLOWS, flow=None, user=current_user()
    )


@app.route("/flow/<slug>")
def flow_detail(slug):
    """ไดอะแกรมเดียวเต็มหน้า — เทมเพลตเดียวกับเมนู เลือกบล็อกด้วย flow.slug"""
    item = FLOWS_BY_SLUG.get(slug)
    if item is None:
        abort(404)
    return render_template(
        "flow.html", flows=FLOWS, flow=item, user=current_user()
    )


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
        return redirect(url_for("viewtitle"))
    interval, shared = interval_for_user(user)
    return render_template(
        "dashboard.html", user=user, interval=interval, interval_shared=shared,
        is_admin=is_admin(user),
    )


# คอลัมน์ที่หน้า admin ใช้ — เขียนชื่อออกมาตรง ๆ ไม่ใช่ SELECT * เพราะก้อนนี้ถูกส่ง
# ออกไปเป็น JSON ให้เบราว์เซอร์ด้วย `credentials` (OAuth token ของทุกคน) ต้องไม่หลุดไป
_ADMIN_COLUMNS = (
    "sub", "email", "video_id", "title_template", "enabled", "last_status", "updated_at",
)


def admin_snapshot(me=None):
    """ทุกอย่างที่หน้า admin แสดง — อ่าน SQLite กับไฟล์โควตาเท่านั้น ไม่ยิง YouTube API"""
    with db() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT {', '.join(_ADMIN_COLUMNS)} FROM users ORDER BY rowid DESC"
            )
        ]
    for row in rows:
        # เทียบ sub แล้วทิ้ง ไม่ส่ง id ของ Google ออกไปกับ JSON ทั้งที่หน้าเว็บไม่ได้ใช้
        row["is_me"] = me is not None and row.pop("sub") == me["sub"]
    return {
        "rows": rows,
        "stats": {
            "total": len(rows),
            "active": sum(1 for r in rows if r["enabled"]),
            "configured": sum(1 for r in rows if r["video_id"]),
        },
        "quota": {
            "used": quota_used(),
            "budget": QUOTA_BUDGET,
            "owner_interval": human_interval(owner_interval_seconds()),
            "shared_interval": f"{shared_interval_minutes()} min",
            "burst": burst_active(),
        },
        "now": datetime.now().strftime("%H:%M:%S"),
    }


@app.route("/admin")
def admin():
    user = current_user()
    if not is_admin(user):
        abort(403)
    return render_template("admin.html", me=user, **admin_snapshot(user))


@app.route("/admin/data")
def admin_data():
    """หน้า admin ดึงซ้ำทุกไม่กี่วินาที

    เป็น polling ไม่ใช่ SSE/WebSocket โดยตั้งใจ — gunicorn รัน `--workers 1`
    (ไม่งั้น scheduler จะซ้อนกัน) connection ที่ค้างไว้จะยึด worker ตัวเดียวที่มี
    แล้วทั้งเว็บหยุดตอบ
    """
    user = current_user()
    if not is_admin(user):
        abort(403)
    return jsonify(admin_snapshot(user))


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
    flash("บันทึกแล้ว", "ok")
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
    flash(f"ทดสอบทันที: {status}", "err" if status_is_error(status) else "ok")
    return redirect(url_for("dashboard"))


@app.route("/channel")
def channel():
    """หน้าสาธารณะ: สถิติช่อง YouTube ของเจ้าของ (มาจาก cache ดู get_channel_stats)"""
    stats = get_channel_stats()
    sub_goal = channel_sub_goal(stats["subs"]) if stats else CHANNEL_SUB_GOAL
    return render_template(
        "channel.html", stats=stats, user=current_user(),
        cache_minutes=CHANNEL_CACHE_TTL // 60, sub_goal=sub_goal,
    )


# ------------------------------------------------------------------- Snake
def count_snake_game_lines():
    """นับบรรทัดโค้ดเกมจริงจากเทมเพลต ระหว่างมาร์ก game-code:start/end

    หน้าเว็บโฆษณาว่าเกมยาวกี่บรรทัด ถ้าพิมพ์ตัวเลขตายตัวไว้ วันหนึ่งแก้เกมแล้ว
    ลืมแก้ตัวเลข หน้าเว็บก็จะโกหกทันที — อ่านจากไฟล์จึงไม่มีทางหลุด
    """
    path = os.path.join(BASE_DIR, "templates", "snake.html")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        start = next(i for i, ln in enumerate(lines) if "game-code:start" in ln)
        end = next(i for i, ln in enumerate(lines) if "game-code:end" in ln)
    except (OSError, StopIteration):
        return None
    return end - start - 1


# อ่านครั้งเดียวตอน import — เทมเพลตไม่เปลี่ยนระหว่างรัน
SNAKE_GAME_LINES = count_snake_game_lines()


def snake_replay(seed, inputs, steps):
    """เล่นเกมซ้ำจาก seed + ลำดับปุ่มที่กด แล้วคืน (score, จบที่สเต็ปไหน)

    ตรรกะทุกบรรทัดต้องตรงกับในเทมเพลต snake.html ถ้าแก้ที่นั่นต้องแก้ที่นี่ด้วย
    ไม่งั้นการเล่นที่ถูกต้องจะถูกปฏิเสธ

    ตัวสุ่มใช้ Park–Miller เพราะผลคูณสูงสุด 16807 × 2^31 ยังไม่ถึง 2^53
    เลข double ของ JS จึงคำนวณได้ตรงกับ int ของ Python เป๊ะ ๆ ไม่มีปัดเศษ
    และหยิบช่องด้วย % (จำนวนเต็ม) ไม่ใช่คูณ float เพื่อตัดความต่างของทศนิยมทิ้งไป
    """
    state = seed % 2147483647
    if state <= 0:
        state += 2147483646

    def rand(n):
        nonlocal state
        state = (state * 16807) % 2147483647
        return state % n

    x, y = 160, 160
    dx, dy = SNAKE_GRID, 0
    cells = deque()
    occupied = set()          # ช่องที่งูทับอยู่ ดูแลแบบเพิ่ม/ลบทีละช่อง
    max_cells = 4
    score = 0
    food = (320, 320)

    def place_food():
        free = [
            (fx, fy)
            for fx in range(0, SNAKE_BOARD, SNAKE_GRID)
            for fy in range(0, SNAKE_BOARD, SNAKE_GRID)
            if (fx, fy) not in occupied
        ]
        return free[rand(len(free))] if free else food

    by_step = {}
    for st, key in inputs:
        by_step.setdefault(st, []).append(key)

    food = place_food()       # resetGame() วางอาหารชิ้นแรกตอนงูยังไม่มีตัว

    for s in range(1, steps + 1):
        for key in by_step.get(s - 1, ()):
            ndx, ndy = SNAKE_KEYS[key]
            if ndx and dx == 0:
                dx, dy = ndx, 0
            elif ndy and dy == 0:
                dx, dy = 0, ndy

        x = (x + dx) % SNAKE_BOARD          # ทะลุขอบไปโผล่อีกฝั่ง
        y = (y + dy) % SNAKE_BOARD
        head = (x, y)

        cells.appendleft(head)
        if len(cells) > max_cells:
            # หัวไปทับ "ช่องที่หางกำลังจะออก" พอดี ไม่นับว่าชน — ตรงกับ JS
            # ที่ pop ก่อนแล้วค่อยเช็คซ้ำ ลำดับตรงนี้จึงห้ามสลับ
            occupied.discard(cells.pop())
        if head in occupied:
            return score, s
        occupied.add(head)

        if head == food:
            max_cells += 1
            score += 10
            food = place_food()

        if max_cells >= SNAKE_CELLS:
            return score, s

    return score, None                       # เล่นครบแล้วยังไม่จบ = ไม่ตรงกับที่อ้าง


def snake_check_inputs(raw):
    """รับ [[step, key], ...] จากเบราว์เซอร์ คืน list ที่สะอาดแล้ว หรือ None ถ้าเชื่อไม่ได้"""
    if not isinstance(raw, list) or len(raw) > SNAKE_MAX_INPUTS:
        return None
    out = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            return None
        st, key = item
        if not isinstance(st, int) or isinstance(st, bool):
            return None
        if not isinstance(key, int) or isinstance(key, bool):
            return None
        if st < 0 or st > SNAKE_MAX_STEPS or key not in SNAKE_KEYS:
            return None
        out.append((st, key))
    return out


def snake_display_name(user):
    """ชื่อที่โชว์บนกระดาน — users ไม่มีคอลัมน์ชื่อ จึงใช้ส่วนหน้า @ ของอีเมล"""
    return (user["email"] or "player").split("@")[0][:12]


def snake_top(limit=10):
    """คะแนนเท่ากันให้คนที่ทำได้ก่อนอยู่บนกว่า — ตัดสินด้วย best_at"""
    with db() as conn:
        rows = conn.execute(
            "SELECT name, score FROM snake_scores"
            " ORDER BY score DESC, best_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def snake_first_perfect():
    """คนแรกที่เก็บเต็มกระดาน (3960 = กินครบทุกช่อง) หรือ None ถ้ายังไม่มีใครทำได้"""
    with db() as conn:
        row = conn.execute(
            "SELECT name, best_at FROM snake_scores WHERE score >= ?"
            " ORDER BY best_at ASC LIMIT 1",
            (SNAKE_MAX_SCORE,),
        ).fetchone()
    if not row:
        return None
    return {
        "name": row["name"],
        "at": datetime.fromtimestamp(row["best_at"]).strftime("%Y-%m-%d"),
    }


@app.route("/snake")
def snake():
    """หน้าสาธารณะ: เกมงู เล่นได้เลย แต่ต้องล็อกอินถึงจะบันทึกคะแนน"""
    return render_template(
        "snake.html", user=current_user(), line_budget=SNAKE_LINE_BUDGET,
        game_lines=SNAKE_GAME_LINES,
    )


@app.route("/api/snake/scores", methods=["GET"])
def snake_scores():
    user = current_user()
    return jsonify({
        "me": snake_display_name(user) if user else None,
        "top": snake_top(),
        "perfect_score": SNAKE_MAX_SCORE,
        "first_perfect": snake_first_perfect(),
    })


@app.route("/api/snake/start", methods=["POST"])
def snake_start():
    """แจก seed หนึ่งใบให้เกมที่กำลังจะเริ่ม — ผลจะถูกรับก็ต่อเมื่ออ้าง game_id ใบนี้"""
    user = current_user()
    if not user:
        return jsonify({"error": "login_required"}), 401

    game_id = secrets.token_urlsafe(16)
    seed = secrets.randbelow(2147483646) + 1
    now = time.time()
    with db() as conn:
        conn.execute("DELETE FROM snake_runs WHERE issued_at < ?",
                     (now - SNAKE_RUN_TTL,))
        conn.execute(
            "INSERT INTO snake_runs (game_id, sub, seed, issued_at) VALUES (?,?,?,?)",
            (game_id, user["sub"], seed, now),
        )
    return jsonify({"game_id": game_id, "seed": seed})


@app.route("/api/snake/scores", methods=["POST"])
def snake_submit():
    user = current_user()
    if not user:
        return jsonify({"error": "login_required"}), 401

    body = request.get_json(silent=True) or {}
    game_id = body.get("game_id")
    steps = body.get("steps")
    inputs = snake_check_inputs(body.get("inputs"))

    if not isinstance(game_id, str) or inputs is None:
        return jsonify({"error": "bad_run"}), 400
    if not isinstance(steps, int) or isinstance(steps, bool):
        return jsonify({"error": "bad_run"}), 400
    if steps <= 0 or steps > SNAKE_MAX_STEPS:
        return jsonify({"error": "bad_run"}), 400

    now = time.time()
    with db() as conn:
        run = conn.execute(
            "SELECT seed, issued_at FROM snake_runs WHERE game_id=? AND sub=?",
            (game_id, user["sub"]),
        ).fetchone()
        # ลบทิ้งทันทีไม่ว่าผลจะผ่านหรือไม่ — หนึ่ง seed เล่นได้ครั้งเดียว
        if run:
            conn.execute("DELETE FROM snake_runs WHERE game_id=?", (game_id,))

    if not run:
        return jsonify({"error": "unknown_run"}), 400

    # เล่นเร็วกว่าที่เกมเดินได้จริงไม่ได้ ต่อให้ input ถูกต้องทุกตัว
    if (now - run["issued_at"]) * 1000 < steps * SNAKE_MIN_MS_PER_STEP:
        return jsonify({"error": "too_fast_to_be_real"}), 400

    # จุดสำคัญ: คะแนนมาจากการเล่นซ้ำฝั่งเซิร์ฟเวอร์ ไม่ได้มาจากเบราว์เซอร์เลย
    score, ended_at = snake_replay(run["seed"], inputs, steps)
    if ended_at != steps:
        return jsonify({"error": "run_did_not_end_there"}), 400
    if score <= 0:
        return jsonify({
            "ok": True, "score": 0, "top": snake_top(),
            "perfect_score": SNAKE_MAX_SCORE,
            "first_perfect": snake_first_perfect(),
        })

    # ไม่ต้องมี cooldown แล้ว: ทุกเกมต้องขอ seed ใหม่จากเซิร์ฟเวอร์ และต้องใช้เวลา
    # เดินจริงตามจำนวนสเต็ป การยิงรัวจึงเป็นไปไม่ได้ตั้งแต่ต้นทาง
    with db() as conn:
        # 1 คน 1 อันดับ ส่งคะแนนต่ำกว่าเดิมมาก็ไม่ทับของเก่า
        # best_at ขยับเฉพาะตอนทำคะแนนได้ดีขึ้นจริง ๆ เท่านั้น เพราะมันคือหลักฐาน
        # ว่าใครเก็บเต็มกระดานได้ก่อน — SQLite อ่านค่าฝั่งขวาจากแถวเดิมทั้งหมด
        # ลำดับของ SET จึงไม่กวนกันเอง
        conn.execute(
            """
            INSERT INTO snake_scores (sub, name, score, best_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sub) DO UPDATE SET
                name       = excluded.name,
                best_at    = CASE WHEN excluded.score > snake_scores.score
                                  THEN excluded.best_at
                                  ELSE snake_scores.best_at END,
                score      = MAX(snake_scores.score, excluded.score),
                updated_at = excluded.updated_at
            """,
            (user["sub"], snake_display_name(user), score, now, now),
        )

    return jsonify({
        "ok": True,
        "score": score,
        "top": snake_top(),
        "perfect_score": SNAKE_MAX_SCORE,
        "first_perfect": snake_first_perfect(),
    })


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
        # คะแนนเกมงูผูกกับ sub และชื่อมาจากอีเมล = ข้อมูลส่วนบุคคล
        # ลบบัญชีแล้วต้องลบด้วย ไม่งั้นขัดกับ /privacy ที่สัญญาว่าลบหมด
        conn.execute("DELETE FROM snake_scores WHERE sub=?", (user["sub"],))
    session.clear()
    return redirect(url_for("viewtitle"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("viewtitle"))


# --------------------------------------------------------------- เริ่มระบบ
init_db()

# สตาร์ท scheduler ครั้งเดียว (ต้องรันด้วย gunicorn --workers 1)
if os.environ.get("RUN_SCHEDULER", "1") == "1":
    start_scheduler()

if __name__ == "__main__":
    # dev เท่านั้น
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
