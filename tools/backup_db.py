#!/usr/bin/env python3
"""สำรองฐานข้อมูล ViewTitle (มี OAuth token ของผู้ใช้ทุกคน — ห้ามหลุด)

ใช้ `sqlite3.Connection.backup()` ไม่ใช่ cp: มันทำ snapshot ที่ consistent
แม้แอปกำลังเขียนอยู่ จึงไม่ได้ไฟล์ที่พังครึ่ง ๆ

เขียนด้วย Python เพราะ:
  - เซิร์ฟเวอร์ไม่มี `sqlite3` CLI (และไม่ต้องลง — sqlite3 เป็น stdlib)
  - เลี่ยงกับดัก GNU vs BSD ใน shell (`head -n -N`, `date -Is`) ที่ต่างกัน
    ระหว่างเซิร์ฟเวอร์กับเครื่อง dev

ติดตั้งบนเซิร์ฟเวอร์เป็น cron รายวัน:
  0 3 * * * /usr/bin/python3 /opt/viewtitle/tools/backup_db.py >> /var/log/viewtitle-backup.log 2>&1

ตัวแปรที่ปรับได้: DB_PATH, BACKUP_DIR, KEEP
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "/opt/viewtitle/web/data.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/opt/viewtitle/backups")
KEEP = int(os.environ.get("KEEP", "14"))


def log(msg):
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    print(f"[{stamp}] {msg}", flush=True)


def die(msg):
    log(f"ERROR: {msg}")
    sys.exit(1)


def main():
    if not os.path.isfile(DB_PATH):
        die(f"ไม่พบ {DB_PATH}")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(BACKUP_DIR, f"data-{stamp}.db")

    # snapshot ที่ consistent แม้แอปกำลังเขียน
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(out, 0o600)

    # ตรวจว่าไฟล์ที่ได้ใช้งานได้จริง ไม่ใช่แค่มีไฟล์
    con = sqlite3.connect(out)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            con.close()
            os.remove(out)
            die(f"{os.path.basename(out)} ไม่ผ่าน integrity_check ({ok}) — ลบทิ้งแล้ว")
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        con.close()

    size_kb = os.path.getsize(out) / 1024

    # ลบไฟล์เก่าเกิน KEEP (ชื่อเรียงตามเวลาเพราะ stamp เป็น YYYYMMDD-HHMMSS)
    snaps = sorted(
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith("data-") and f.endswith(".db")
    )
    for old in snaps[:max(0, len(snaps) - KEEP)]:
        os.remove(os.path.join(BACKUP_DIR, old))
        log(f"ลบ backup เก่า: {old}")

    kept = len([f for f in os.listdir(BACKUP_DIR)
                if f.startswith("data-") and f.endswith(".db")])
    log(f"OK: {os.path.basename(out)} ({size_kb:.0f}K, {users} users) — เก็บไว้ {kept} ไฟล์")


if __name__ == "__main__":
    main()
