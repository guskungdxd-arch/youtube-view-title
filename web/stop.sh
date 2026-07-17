#!/bin/bash
# ปิด web server — พิมพ์ ./stop.sh
pkill -f "web/venv/bin/python app.py" 2>/dev/null
sleep 1
if pgrep -f "web/venv/bin/python app.py" > /dev/null; then
  echo "⚠️  ยังมี process เหลืออยู่ ลองรันซ้ำอีกครั้ง"
else
  echo "✅ ปิด server เรียบร้อย"
fi
