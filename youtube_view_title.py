#!/usr/bin/env python3
"""
เปลี่ยนชื่อคลิป YouTube ให้ตามยอดวิว (แบบไม่ real-time)

ตัวอย่างชื่อที่ได้:  "คลิปนี้ของผมมียอดวิว 12,345 วิว"

โหมดการใช้งาน:
  python youtube_view_title.py            # อัปเดตครั้งเดียว (เหมาะกับ cron)
  python youtube_view_title.py --loop     # วนอัปเดตทุก ๆ N นาที ตาม config
  python youtube_view_title.py --dry-run  # ลองดูว่าจะเปลี่ยนเป็นชื่ออะไร โดยไม่แก้จริง
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ต้องใช้สิทธิ์ระดับแก้ไข (write) เพื่อเปลี่ยนชื่อคลิป
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
CLIENT_SECRET_FILE = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_config():
    if not os.path.exists(CONFIG_FILE):
        sys.exit(
            f"ไม่พบไฟล์ config.json — ให้คัดลอกจาก config.example.json มาแก้ก่อน\n"
            f"  cp config.example.json config.json"
        )
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)

    cfg.setdefault("title_template", "คลิปนี้ของผมมียอดวิว {views} วิว")
    cfg.setdefault("update_interval_minutes", 60)
    cfg.setdefault("min_change_to_update", 1)

    if not cfg.get("video_id") or cfg["video_id"].startswith("ใส่_"):
        sys.exit("กรุณาตั้งค่า video_id ในไฟล์ config.json ก่อน")
    return cfg


def get_authenticated_service():
    """ทำ OAuth ครั้งแรกผ่านเบราว์เซอร์ แล้วเก็บ token ไว้ใช้ครั้งต่อไป"""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                sys.exit(
                    "ไม่พบไฟล์ client_secret.json\n"
                    "ดูวิธีสร้างได้ในไฟล์ README.md (ขั้นตอนที่ 2)"
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def fetch_video(youtube, video_id):
    """ดึงยอดวิว + ข้อมูล snippet ปัจจุบันของคลิป"""
    resp = (
        youtube.videos()
        .list(part="snippet,statistics", id=video_id)
        .execute()
    )
    items = resp.get("items", [])
    if not items:
        sys.exit(f"ไม่พบคลิป id={video_id} (คลิปนี้เป็นของบัญชีที่ล็อกอินหรือไม่?)")
    item = items[0]
    views = int(item["statistics"].get("viewCount", 0))
    return item["snippet"], views


def build_new_title(template, views):
    return template.format(views=f"{views:,}")


def update_title(youtube, video_id, snippet, new_title):
    """อัปเดตเฉพาะชื่อ โดยรักษา description / tags / categoryId เดิมไว้"""
    body = {
        "id": video_id,
        "snippet": {
            "title": new_title,
            # categoryId เป็น field บังคับตอน update — ต้องส่งกลับไปด้วย
            "categoryId": snippet.get("categoryId", "22"),
            "description": snippet.get("description", ""),
            "tags": snippet.get("tags", []),
            "defaultLanguage": snippet.get("defaultLanguage"),
        },
    }
    # ลบ field ที่เป็น None ออก
    body["snippet"] = {k: v for k, v in body["snippet"].items() if v is not None}
    youtube.videos().update(part="snippet", body=body).execute()


def run_once(youtube, cfg, dry_run=False):
    video_id = cfg["video_id"]
    snippet, views = fetch_video(youtube, video_id)
    current_title = snippet.get("title", "")
    new_title = build_new_title(cfg["title_template"], views)

    log(f"ยอดวิวปัจจุบัน: {views:,}")
    log(f"ชื่อเดิม : {current_title}")
    log(f"ชื่อใหม่ : {new_title}")

    if current_title == new_title:
        log("ชื่อไม่เปลี่ยน — ข้าม")
        return

    if dry_run:
        log("(dry-run) ไม่ได้อัปเดตจริง")
        return

    try:
        update_title(youtube, video_id, snippet, new_title)
        log("✅ อัปเดตชื่อคลิปเรียบร้อย")
    except HttpError as e:
        log(f"❌ อัปเดตไม่สำเร็จ: {e}")


def main():
    parser = argparse.ArgumentParser(description="เปลี่ยนชื่อคลิป YouTube ตามยอดวิว")
    parser.add_argument("--loop", action="store_true", help="วนอัปเดตตาม interval ใน config")
    parser.add_argument("--dry-run", action="store_true", help="ลองดูผลโดยไม่แก้จริง")
    args = parser.parse_args()

    cfg = load_config()
    youtube = get_authenticated_service()

    if not args.loop:
        run_once(youtube, cfg, dry_run=args.dry_run)
        return

    interval = cfg["update_interval_minutes"] * 60
    log(f"เริ่มโหมด loop — อัปเดตทุก {cfg['update_interval_minutes']} นาที (Ctrl+C เพื่อหยุด)")
    while True:
        try:
            run_once(youtube, cfg, dry_run=args.dry_run)
        except Exception as e:  # noqa: BLE001 — กัน loop ตายกลางทาง
            log(f"เกิดข้อผิดพลาด: {e}")
        log(f"รอ {cfg['update_interval_minutes']} นาที...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
