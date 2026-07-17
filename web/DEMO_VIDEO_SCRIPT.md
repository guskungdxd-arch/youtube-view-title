# สคริปต์อัดวิดีโอ demo (สำหรับ Google OAuth Verification)

Google มักขอวิดีโอที่แสดง (1) หน้า OAuth consent ที่มีชื่อแอป + scope ชัดเจน
(2) การใช้งาน scope จริง อัดหน้าจอ ~1-3 นาที ภาษาอังกฤษหรือไทยก็ได้ (อังกฤษปลอดภัยกว่า)

## สิ่งที่ต้องโชว์ในวิดีโอ (ห้ามขาด)

1. **URL ของแอป** — โชว์ address bar ว่าเป็นโดเมนคุณ (`https://yourdomain.com`)
2. **หน้า OAuth consent** — ตอนกดล็อกอิน โชว์หน้าจอ Google ที่ขึ้น:
   - ชื่อแอป (App name)
   - scope ที่ขอ: `.../auth/youtube.force-ssl`
3. **การใช้ scope จริง** — หลังล็อกอิน โชว์ว่าแอปเอา scope นั้นไปทำอะไร:
   - ใส่ video ID → กด "อัปเดตทันที" → ชื่อคลิปบน YouTube เปลี่ยนเป็นยอดวิว
4. (แนะนำ) โชว์ปุ่ม **ลบบัญชี** ว่าผู้ใช้ถอนสิทธิ์/ลบข้อมูลได้

## สคริปต์พูด (ตัวอย่าง — อังกฤษ)

> "This is ViewTitle, hosted at yourdomain.com. It automatically updates a
> YouTube video's title to show its current view count.
>
> When a user signs in with Google, we request the youtube.force-ssl scope.
> [โชว์หน้า consent] You can see the app name and the requested scope here.
>
> After granting access, the user enters their video ID and saves.
> [กด Save แล้ว Update now] The app reads the video's view count and updates
> its title — as you can see, the title on YouTube now shows the view count.
>
> We only use this scope to read view counts and update titles the user owns.
> Users can delete their account and revoke access anytime from the dashboard.
> [โชว์ปุ่มลบบัญชี] Our privacy policy is at yourdomain.com/privacy."

## เทคนิคอัด
- Mac: กด **Cmd + Shift + 5** เลือกอัดหน้าจอ
- อย่าโชว์ client_secret / token / รหัสผ่าน ในจอ
- อัปโหลดขึ้น YouTube แบบ **Unlisted** แล้วแปะลิงก์ตอน submit
