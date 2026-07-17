# Checklist เตรียม deploy + เปิด public

## ✅ เตรียมไว้แล้ว (Claude ทำให้)
- [x] เว็บแอปหลายผู้ใช้ (OAuth, dashboard, admin) ทำงานได้
- [x] หน้า **Privacy Policy** (`/privacy`) + **Terms** (`/terms`) — บังคับสำหรับ verification
- [x] ปุ่ม **ลบบัญชี + ถอนสิทธิ์ token** (`/delete-account`) — บังคับสำหรับ verification
- [x] Limited Use disclosure ในหน้า privacy — บังคับสำหรับ verification
- [x] แยก secret ออกเป็น env vars (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `FLASK_SECRET`)
- [x] `.gitignore` กันไฟล์ลับหลุด + ลบ demo ปลอมออกจาก DB
- [x] `render.yaml` config พร้อม deploy
- [x] git repo + commit แรกเรียบร้อย

## 📋 คุณต้องทำเอง (ตามลำดับ)

### ช่วงนี้ทำได้เลย (ฟรี)
- [ ] สร้าง repo บน **GitHub** แล้ว push:
  ```bash
  cd /Users/mac/youtube_change_name
  git remote add origin https://github.com/<user>/<repo>.git
  git branch -M main
  git push -u origin main
  ```
- [ ] ทดสอบกับเพื่อน 1-2 คน (เพิ่มเป็น Test user ใน Google Console)
- [ ] ตัดสินใจ **ชื่อแอปจริง + โลโก้** (ตอนนี้ "ViewTitle" ชั่วคราว)
- [ ] ออกแบบ UI ให้เสร็จ (งานที่คุณจะทำเอง)

### ตอนใกล้ deploy จริง
- [ ] ซื้อ **โดเมน** (~300฿/ปี) — ถ้าจะทำ verification
- [ ] สร้าง **OAuth client แบบ Web application** ใน Google Console
      + redirect URI = `https://<โดเมน>/oauth2callback`
- [ ] Deploy ขึ้น **Render** (New → Blueprint → เลือก repo)
      ตั้ง env: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `BASE_URL`, `CONTACT_EMAIL`
- [ ] ตั้ง `RUN_SCHEDULER=1` บน production (บนเครื่อง dev ปิดไว้)

### เปิดให้คนแปลกหน้า (เลือก 1 ทาง)
- [ ] **ทางฟรี:** Google Console → Publish app → Production (ไม่ verify)
      → คนอื่นใช้ได้แต่เจอหน้าเตือน "unverified"
- [ ] **ทางจ่าย:** ทำ verification (privacy policy พร้อมแล้ว) + verify โดเมน + อัดวิดีโอ demo
      → รอ Google รีวิว 2-3 สัปดาห์ → ไม่มีหน้าเตือน

## ⚠️ อย่าลืม
- production ต้องใช้ **Production mode** ไม่ใช่ Testing (ไม่งั้น token หมดอายุใน 7 วัน)
- ห้าม push `client_secret.json`, `token.json`, `.env`, `data.db` (gitignore กันให้แล้ว)
