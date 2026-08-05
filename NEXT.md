# สถานะปัจจุบัน / งานที่ค้าง

อัปเดต 1 ส.ค. 2026 — ไฟล์นี้ไว้หยิบงานต่อจากอีกเครื่อง
(ความลับไม่ได้อยู่ในนี้และไม่อยู่ใน repo ดูหัวข้อ "ตั้งเครื่องใหม่" ข้างล่าง)

## 🔴 ค้างและมีเดดไลน์

**ย้าย VM ก่อน ~20 ส.ค. 2026**
shape ปัจจุบันไม่ใช่ Always Free พอ trial หมดจะโดนเก็บ ~$40-50/เดือน
ต้องเลือก: ย้ายไป `VM.Standard.A1.Flex` (ARM ฟรี) / ลบทิ้ง / ยอมจ่าย
ถ้าย้าย = ตั้ง nginx + certbot + systemd + env ใหม่ทั้งชุด และ**ย้าย `data.db` ให้ครบ**
backup ตอนนี้อยู่บน VM เดียวกับตัวจริง จึงกันเครื่องหายไม่ได้ — ควรดึงสำเนาออกนอกก่อนย้าย

**ชื่อคลิปไม่ได้อัปเดตตั้งแต่ 16 ก.ค. 2026**
CLI บนเครื่อง Mac ล้มเหลว 198 ครั้งด้วย `invalid_grant: Token has been expired or revoked`
เพราะ `token.json` ถูกออกตอน OAuth app ยังเป็นโหมด Testing (refresh token อายุ 7 วัน)
launchd agent ถูก `launchctl unload` ไปแล้ว ฝั่งเว็บก็ไม่ได้ทำงานเพราะแถวผู้ใช้
ยังไม่ได้ตั้ง `video_id` และ `enabled = 0`

ตัดสินใจไว้แล้วว่า **ให้เว็บทำแทน CLI** เหลือ 3 ขั้นที่ต้องทำเอง:
1. ล็อกอินที่เว็บ (ผ่านหน้าเตือน unverified ด้วย Advanced) — ขั้นนี้ออก refresh token ใบใหม่
2. dashboard → ใส่ Video ID + ติ๊ก Update automatically + Save
3. กด "Update now" เพื่อเขียนทันที ไม่ต้องรอรอบ

⚠️ อย่าเปิด launchd กลับพร้อมกับใช้เว็บ — สองตัวจะแย่งกันเขียนชื่อเดียวกัน
และทั้งคู่ใช้ Google Cloud project เดียวกัน แชร์โควตา 10,000/วัน ก้อนเดียว
(เอา CLI กลับ: `launchctl load ~/Library/LaunchAgents/com.user.ytviewtitle.plist`)

## 🟡 ค้าง ไม่เร่ง

**Deploy ฟอนต์ไทย** — commit แล้วแต่ยังไม่ได้ pull ขึ้นเซิร์ฟเวอร์

**Quota: delta threshold** — scheduler ฉลาดแล้ว (นับหน่วย, แยกเลนเจ้าของ, batch read,
หยุดเมื่องบหมด) แต่ `build_title()` ยังใส่เลขเป๊ะ ชื่อจึงเปลี่ยนแทบทุกรอบ = จ่าย 50 หน่วยทุกรอบ
**ห้ามเสนอ "ปัดยอดวิว"** — เจ้าของปฏิเสธแล้ว ความเท่อยู่ที่เลขเป๊ะทุกหลัก
ทางที่ตกลงกันไว้คือเก็บ `last_written_views` แล้วเขียนเมื่อขยับถึงเกณฑ์ หรือครบ X ชม.
ประหยัดเท่าการปัด แต่ชื่อยังโชว์เลขเต็ม

**Google verification** — ถ้าจะไปต่อต้องคืน branding ให้ตรง Console (`<h1>ViewTitle</h1>`,
Application home page ชี้ `/viewtitle`, อัปโลโก้ใหม่) + อัดวิดีโอ demo ตาม
`web/DEMO_VIDEO_SCRIPT.md` — ถ้ายังตั้งใจอยู่แค่กลุ่มเล็ก ≤100 คน ข้ามได้

**อื่น ๆ** — ฟอนต์ไทยแบบ pixel จริง (ต้องหาไฟล์มาโฮสต์เอง + อ่านเงื่อนไข web embedding),
เกม Doom ที่ `/play` (ยังติดปัญหา asset 20-40MB และไม่มี emcc),
`web/static/images.jpeg` ที่ยังไม่รู้ว่าจะใช้ทำอะไร (ไม่ได้ commit)

## ✅ เพิ่งเสร็จ

- `/flow` โปรเจกต์ที่ 3 — ไดอะแกรมอธิบายระบบ 3 อัน (`/flow/<slug>`) พร้อมมิเตอร์โควตา
  ที่นับถอยหลังไปพร้อมแถบ · deploy แล้ว
- ฟอนต์ไทยทุกหน้า (Chakra Petch ต่อท้าย stack) · commit แล้ว รอ deploy

## ตั้งเครื่องใหม่

ของที่ **ไม่ได้อยู่ใน git** ต้องเตรียมเอง:

| ไฟล์ | เอามาจากไหน |
|--|--|
| `client_secret.json` | Google Cloud Console → OAuth client (Desktop สำหรับ CLI) |
| `config.json` | ก๊อปจาก `config.example.json` แล้วใส่ video id |
| `token.json` | สร้างใหม่ด้วย `./venv/bin/python authorize.py` |
| `venv/`, `web/venv/` | `python3 -m venv venv && ./venv/bin/pip install -r requirements.txt` |
| ssh key ของเซิร์ฟเวอร์ | ก๊อปจากเครื่องเดิม — ไม่มีคีย์ = deploy ไม่ได้ |

**บันทึกของ Claude ไม่ได้ย้ายตาม git** อยู่ที่
`~/.claude/projects/-Users-mac-youtube-change-name/memory/` บนเครื่องเดิม
ถ้าอยากได้บริบทเดิมบนเครื่องใหม่ต้องก๊อปโฟลเดอร์นั้นไปเอง

รันเว็บในเครื่อง: `cd web && ./start.sh` (ตั้ง `RUN_SCHEDULER=0` ให้อัตโนมัติ
จะได้ไม่ไปแก้ชื่อคลิปจริง) ปิดด้วย `./stop.sh` · เทสต์: `./web/venv/bin/python tests/test_quota_scheduling.py`
