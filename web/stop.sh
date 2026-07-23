#!/bin/bash
# ปิด web server — พิมพ์ ./stop.sh
# ฆ่าตาม port 5055 ที่ listen อยู่จริง (argv ของ process เป็น path framework python
# ไม่ใช่ "web/venv/bin/python" การ pkill -f ตาม path เดิมจึงไม่เคย match)
pids=$(lsof -ti:5055 -sTCP:LISTEN 2>/dev/null)
if [ -z "$pids" ]; then
  echo "✅ server ไม่ได้เปิดอยู่"
  exit 0
fi

kill $pids 2>/dev/null
sleep 1
# ถ้ายังไม่ตาย ค่อยบังคับ
pids=$(lsof -ti:5055 -sTCP:LISTEN 2>/dev/null)
if [ -n "$pids" ]; then
  kill -9 $pids 2>/dev/null
  sleep 1
  pids=$(lsof -ti:5055 -sTCP:LISTEN 2>/dev/null)
fi

if [ -z "$pids" ]; then
  echo "✅ ปิด server เรียบร้อย"
else
  echo "⚠️  ยังมี process ถือ port 5055 อยู่ (pid: $pids)"
  exit 1
fi
