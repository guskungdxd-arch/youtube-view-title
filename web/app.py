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
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
]

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
    print(
        f"[{datetime.now():%H:%M:%S}] run_owner: {len(owner_rows)} คน "
        f"ใช้ {spent} หน่วย (ใช้ไปวันนี้ {quota_used()}/{QUOTA_BUDGET})",
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


# ------------------------------------------------------- คุม scheduler / จังหวะ
# APScheduler ตั้ง interval ครั้งเดียวตอน add_job — ถ้าอยากให้เปลี่ยนจริงต้อง
# reschedule() ทุกรอบ ไม่ใช่แค่เปลี่ยนค่าตัวแปร
scheduler = None
owner_job = None
shared_job = None


def _wait_for_reset_minutes(now=None):
    """ตอนข้ามรอบเพราะโควตาหมด ให้ตื่นอีกทีหลังโควตารีเซ็ต (การเช็คไม่กินโควตา)"""
    return max(5, min(MAX_INTERVAL_MINUTES, math.ceil(minutes_until_quota_reset(now)) + 1))


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
        run_owner, "interval", minutes=OWNER_INTERVAL_MINUTES,
        id="owner", max_instances=1, coalesce=True,
    )
    shared_job = scheduler.add_job(
        run_shared, "interval", minutes=shared_interval_minutes(),
        id="shared", max_instances=1, coalesce=True,
    )
    scheduler.start()
    print(
        f"[scheduler] เลนเจ้าของทุก {OWNER_INTERVAL_MINUTES} นาที, "
        f"เลนรวมเริ่มที่ {shared_interval_minutes()} นาที "
        f"(งบ {QUOTA_BUDGET} หน่วย/วัน, ใช้ไปแล้ว {quota_used()})",
        flush=True,
    )
    return scheduler


def interval_for_user(user):
    """interval ที่ใช้กับผู้ใช้คนนี้จริง ๆ — เอาไปโชว์บน dashboard ให้ตรงความจริง"""
    if user and user["sub"] in owner_subs():
        return OWNER_INTERVAL_MINUTES, False
    return shared_interval_minutes(), True


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
    return render_template(
        "channel.html", stats=get_channel_stats(), user=current_user(),
        cache_minutes=CHANNEL_CACHE_TTL // 60,
    )


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
