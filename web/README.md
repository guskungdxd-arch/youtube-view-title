# เว็บ: คลิปเปลี่ยนชื่อตามยอดวิว (หลายผู้ใช้)

เว็บแอปที่ให้ใครก็ได้ล็อกอิน Google ของตัวเอง ใส่ video_id แล้วระบบเปลี่ยนชื่อคลิป
เป็นยอดวิวล่าสุดให้อัตโนมัติ เช่น `คลิปนี้ของผมมียอดวิว 12,345 วิว`

> **โหมดกลุ่มเล็ก (≤100 คน)** — ไม่ต้องผ่าน Google verification แค่เพิ่มคนเป็น
> "Test users" ในคอนโซล ผู้ใช้จะเจอหน้าเตือน "unverified app" แต่กด Advanced ผ่านได้

---

## สถาปัตยกรรมย่อ ๆ

- **Flask** เว็บ + OAuth (`Sign in with Google`)
- **SQLite** เก็บ token + ตั้งค่าของแต่ละผู้ใช้
- **APScheduler** งานเบื้องหลัง วนอัปเดตชื่อคลิปทุก N นาที

---

## ส่วนที่ 1 — สร้าง OAuth client แบบ Web

ของเดิมเราสร้างเป็น **Desktop app** แต่เว็บต้องใช้แบบ **Web application**:

1. https://console.cloud.google.com/ → โปรเจกต์เดิม → **APIs & Services → Credentials**
2. **Create Credentials → OAuth client ID → Web application**
3. **Authorized redirect URIs** เพิ่ม 2 อัน:
   - `http://localhost:5000/oauth2callback`  (ไว้ทดสอบบนเครื่อง)
   - `https://ชื่อแอปคุณ.onrender.com/oauth2callback`  (URL จริงหลัง deploy — ค่อยกลับมาเพิ่มทีหลังได้)
4. Create → จด **Client ID** กับ **Client secret** ไว้

> อย่าลืม: **OAuth consent screen → Audience → Test users** เพิ่มอีเมลคนที่จะให้ใช้ (รวมตัวคุณ)

## ส่วนที่ 2 — ทดสอบบนเครื่องก่อน (localhost)

```bash
cd web
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # แล้วเปิดแก้ใส่ CLIENT_ID / SECRET / FLASK_SECRET
set -a && source .env && set +a   # โหลด env เข้า shell
python app.py
```

เปิด http://localhost:5000 → กดล็อกอิน → ใส่ video_id → กด "อัปเดตทันที" ทดสอบ

## ส่วนที่ 3 — Deploy ขึ้น cloud ฟรี (Render.com)

1. push โปรเจกต์นี้ขึ้น GitHub
2. https://render.com → **New + → Blueprint** → เลือก repo (มี `web/render.yaml` ให้แล้ว)
3. ตั้ง Environment Variables: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `BASE_URL`
   (`BASE_URL` = URL ที่ Render ให้ เช่น `https://youtube-view-title.onrender.com`)
4. กลับไป Google Console เพิ่ม redirect URI: `https://<URL นั้น>/oauth2callback`
5. เปิด URL → พร้อมใช้!

> **ข้อควรรู้ Render free**: เว็บจะ "หลับ" เมื่อไม่มีคนเข้า 15 นาที ทำให้ scheduler
> อาจไม่รันตรงเวลา ถ้าอยากให้อัปเดตชัวร์ตลอด ใช้แผนมีค่าใช้จ่าย หรือย้าย scheduler
> ไปเป็น cron job แยก (บอกผมถ้าอยากทำ)

---

## ไฟล์ที่ห้าม commit ขึ้น public

`.env`, `data.db` — มี secret + token ของผู้ใช้ (มีใน `.gitignore` ให้แล้ว)
