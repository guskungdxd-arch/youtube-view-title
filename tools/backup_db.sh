#!/bin/bash
# สำรองฐานข้อมูล ViewTitle (มี OAuth token ของผู้ใช้ทุกคน — ห้ามหลุด)
#
# ใช้ `sqlite3 .backup` ไม่ใช่ cp: มันล็อกไฟล์ให้ถูกวิธี จึงสำรองได้
# ขณะแอปยังเขียนอยู่โดยไม่ได้ไฟล์ที่พังครึ่ง ๆ
#
# ติดตั้งบนเซิร์ฟเวอร์เป็น cron รายวัน:
#   0 3 * * * /opt/viewtitle/tools/backup_db.sh >> /var/log/viewtitle-backup.log 2>&1
#
# ตัวแปรที่ปรับได้: DB_PATH, BACKUP_DIR, KEEP

set -euo pipefail

DB_PATH="${DB_PATH:-/opt/viewtitle/web/data.db}"
BACKUP_DIR="${BACKUP_DIR:-/opt/viewtitle/backups}"
KEEP="${KEEP:-14}"          # เก็บกี่ไฟล์ล่าสุด

stamp=$(date +%Y%m%d-%H%M%S)
out="$BACKUP_DIR/data-$stamp.db"

if [ ! -f "$DB_PATH" ]; then
  echo "[$(date +%FT%T%z)] ERROR: ไม่พบ $DB_PATH" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# .backup ทำ snapshot ที่ consistent แม้แอปกำลังเขียน
sqlite3 "$DB_PATH" ".backup '$out'"
chmod 600 "$out"

# ตรวจว่าไฟล์ที่ได้ใช้งานได้จริง ไม่ใช่แค่มีไฟล์
if ! sqlite3 "$out" "PRAGMA integrity_check;" | grep -q '^ok$'; then
  echo "[$(date +%FT%T%z)] ERROR: $out ไม่ผ่าน integrity_check — ลบทิ้ง" >&2
  rm -f "$out"
  exit 1
fi

users=$(sqlite3 "$out" "SELECT COUNT(*) FROM users;")
size=$(du -h "$out" | cut -f1)

# ลบไฟล์เก่าเกิน KEEP (เรียงตามชื่อ = เรียงตามเวลา เพราะ stamp เป็น YYYYMMDD)
# หมายเหตุ: ใช้ head -n จำนวนบวก ไม่ใช่ `head -n -N` ซึ่งเป็น GNU-only
# (พังบน macOS/BSD) — แบบนี้รันได้ทั้งบนเซิร์ฟเวอร์และบนเครื่อง dev
total=$(ls -1 "$BACKUP_DIR"/data-*.db 2>/dev/null | wc -l | tr -d ' ')
if [ "$total" -gt "$KEEP" ]; then
  ls -1 "$BACKUP_DIR"/data-*.db | sort | head -n "$((total - KEEP))" | while read -r old; do
    rm -f "$old"
    echo "[$(date +%FT%T%z)] ลบ backup เก่า: $(basename "$old")"
  done
fi

echo "[$(date +%FT%T%z)] OK: $(basename "$out") ($size, $users users) — เก็บไว้ $(ls -1 "$BACKUP_DIR"/data-*.db | wc -l | tr -d ' ') ไฟล์"
