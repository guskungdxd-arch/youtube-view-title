#!/bin/bash
# เปิด web server — พิมพ์ ./start.sh
cd "$(dirname "$0")" || exit 1

# ดึง client id/secret จาก client_secret.json ที่อยู่โฟลเดอร์แม่
export GOOGLE_CLIENT_ID=$(python3 -c "import json;print(json.load(open('../client_secret.json'))['installed']['client_id'])")
export GOOGLE_CLIENT_SECRET=$(python3 -c "import json;print(json.load(open('../client_secret.json'))['installed']['client_secret'])")
export FLASK_SECRET=localdev123
export BASE_URL=http://localhost:5055
export PORT=5055
export OAUTHLIB_INSECURE_TRANSPORT=1
# ปิดตัวรันอัตโนมัติตอนรันบนเครื่อง dev — กันไม่ให้มันแก้ชื่อคลิปจริงเองระหว่างทดสอบ
# อยากให้อัปเดตอัตโนมัติ ให้เปลี่ยนเป็น 1 (ตอน deploy จริงเปิดเป็น 1 เสมอ)
export RUN_SCHEDULER=0

# กันเปิดซ้ำ
if pgrep -f "web/venv/bin/python app.py" > /dev/null || lsof -i:5055 -sTCP:LISTEN > /dev/null 2>&1; then
  echo "⚠️  server เปิดอยู่แล้วที่ http://localhost:5055"
  exit 0
fi

# เปิดแบบ background เขียน log ลง server.log
nohup ./venv/bin/python app.py > server.log 2>&1 &
sleep 2
if lsof -i:5055 -sTCP:LISTEN > /dev/null 2>&1; then
  echo "✅ เปิด server แล้ว → http://localhost:5055"
  echo "   ดู log: tail -f web/server.log"
else
  echo "❌ เปิดไม่สำเร็จ ดู log ได้ที่ web/server.log"
  tail -5 server.log
fi
