# คลิปที่ชื่อเปลี่ยนตามยอดวิว (YouTube auto-title by views)

สคริปต์เปลี่ยนชื่อคลิป YouTube ให้แสดงยอดวิวปัจจุบัน เช่น
`คลิปนี้ของผมมียอดวิว 12,345 วิว` — ทำงานเป็นรอบ ๆ (ไม่ real-time)
เหมือนคลิปดัง ๆ ของ Tom Scott

> YouTube API ไม่ได้อัปเดตยอดวิวแบบวินาทีต่อวินาทีอยู่แล้ว และการแก้ชื่อบ่อยเกินไป
> จะกิน quota ทิ้ง การรันทุก 30–60 นาทีถือว่ากำลังดี

---

## ขั้นตอนที่ 1 — ติดตั้ง

```bash
cd /Users/mac/youtube_change_name
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## ขั้นตอนที่ 2 — สร้าง credential (client_secret.json)

1. เข้า https://console.cloud.google.com/ → สร้าง Project ใหม่
2. เมนู **APIs & Services → Library** → ค้นหา **YouTube Data API v3** → กด **Enable**
3. เมนู **APIs & Services → OAuth consent screen**
   - เลือก **External** → กรอกชื่อแอป/อีเมล
   - ในหน้า **Test users** ให้ **เพิ่มอีเมล Google ของคุณ** (บัญชีเดียวกับที่เป็นเจ้าของคลิป)
4. เมนู **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - กด **Download JSON** แล้วเปลี่ยนชื่อไฟล์เป็น `client_secret.json`
   - วางไว้ในโฟลเดอร์เดียวกับสคริปต์นี้

## ขั้นตอนที่ 3 — ตั้งค่า config

```bash
cp config.example.json config.json
```

แก้ไฟล์ `config.json`:

| ค่า | ความหมาย |
|-----|----------|
| `video_id` | ID ของคลิป (ส่วนหลัง `watch?v=` ใน URL) เช่น `dQw4w9WgXcQ` |
| `title_template` | รูปแบบชื่อ ใช้ `{views}` แทนตำแหน่งยอดวิว |
| `update_interval_minutes` | ระยะเวลาระหว่างรอบ (ใช้ตอน `--loop`) |

## ขั้นตอนที่ 4 — รัน

```bash
# ลองก่อน ไม่แก้จริง (ครั้งแรกจะเปิดเบราว์เซอร์ให้ล็อกอิน Google)
python youtube_view_title.py --dry-run

# อัปเดตจริงครั้งเดียว
python youtube_view_title.py

# วนอัปเดตทุก N นาที
python youtube_view_title.py --loop
```

ครั้งแรกจะเปิดเบราว์เซอร์ให้อนุญาต แล้วเก็บ `token.json` ไว้ ครั้งต่อไปไม่ต้องล็อกอินซ้ำ

---

## ตั้งเวลาให้รันอัตโนมัติ (แนะนำ แทน `--loop`)

ใช้ `cron` บน macOS — รันทุกชั่วโมง:

```bash
crontab -e
```

เพิ่มบรรทัด (แก้ path venv ให้ตรงเครื่องคุณ):

```
0 * * * * cd /Users/mac/youtube_change_name && ./venv/bin/python youtube_view_title.py >> cron.log 2>&1
```

---

## เกร็ด / ข้อควรระวัง

- **Quota**: การอ่านยอดวิวถูกมาก แต่การ `update` ชื่อกินโควตาพอควร (~50 units/ครั้ง จาก 10,000/วัน) — รันทุกชั่วโมงสบาย ๆ
- **ต้องเป็นเจ้าของคลิป** — บัญชีที่ล็อกอินต้องเป็นเจ้าของคลิปนั้น
- สคริปต์จะข้ามการอัปเดตถ้าชื่อยังเหมือนเดิม (ไม่เปลืองโควตา)
- ระวังพวก field เดิมของคลิป (คำอธิบาย / แท็ก) — สคริปต์รักษาไว้ให้แล้ว

## ไฟล์ที่ห้ามแชร์ / commit

`client_secret.json`, `token.json`, `config.json` — มีข้อมูลลับ อย่าอัปขึ้น public repo
