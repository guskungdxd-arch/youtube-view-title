#!/usr/bin/env python3
"""โลโก้การ์ด Channel Stats — ไอคอน "คน" แบบ pixel art

Pillow อยู่ใน interpreter ของระบบ ไม่ใช่ venv ของโปรเจกต์:
    /usr/local/bin/python3 tools/make_avatar_logo.py

วาดบนกริด 16x16 เท่ากับ make_logo_pixel.py (โลโก้ "67") โดยตั้งใจ —
สองการ์ดอยู่ข้างกันบนหน้าแรก ถ้ากริดไม่เท่ากันเม็ดพิกเซลจะดูไม่เท่ากัน
ใช้พื้นน้ำเงิน + bevel + มุมตัดชุดเดียวกันด้วย ให้อ่านเป็นมาร์คตระกูลเดียวกัน

เขียนไฟล์: web/static/logo-channel.png (16*8 = 128px)
"""
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "web", "static")

G = 16          # กริดตรรกะ — ต้องเท่ากับ make_logo_pixel.py
SCALE = 8       # 16*8 = 128px

# พาเลตต์เดียวกับโลโก้ "67" เพื่อให้เป็นชุดเดียวกัน
PLATE = (0x00, 0x71, 0xE3, 255)
HI = (0x53, 0xA6, 0xFF, 255)      # bevel บน/ซ้าย
LO = (0x00, 0x52, 0xBD, 255)      # bevel ล่าง/ขวา
FIG = (0xF2, 0xEC, 0xD8, 255)     # ตัวคน — ครีม (--surface ของ ARCADE DAY)
FIG_LO = (0xC5, 0xBC, 0xA2, 255)  # เงาใต้ตัวคน 1 พิกเซล ให้มีมิติ
CLEAR = (0, 0, 0, 0)

# ไอคอนคน: หัวกลม + ลำตัวบ่ากว้างสอบลง (ทรงเดียวกับไอคอนโปรไฟล์มาตรฐาน)
# '#' = ตัวคน, '.' = พื้น
FIGURE = [
    "................",
    "................",
    "......####......",
    ".....######.....",
    "....########....",
    "....########....",
    ".....######.....",
    "......####......",
    "................",
    "...##########...",
    "..############..",
    ".##############.",
    ".##############.",
    "..############..",
    "...##########...",
    "................",
]


def build_grid():
    px = [[PLATE for _ in range(G)] for _ in range(G)]

    # bevel 1px: สว่างบน/ซ้าย เข้มล่าง/ขวา
    for i in range(G):
        px[0][i] = HI
        px[i][0] = HI
        px[G - 1][i] = LO
        px[i][G - 1] = LO

    # มุมตัด 1px ให้ดูเป็น pixel tile มน ๆ
    for (y, x) in [(0, 0), (0, G - 1), (G - 1, 0), (G - 1, G - 1)]:
        px[y][x] = CLEAR

    body = {(x, y) for y, row in enumerate(FIGURE)
            for x, c in enumerate(row) if c == "#"}

    # เงา 1 พิกเซลใต้ขอบล่างของตัวคน (วาดก่อน ตัวคนจะทับทีหลัง)
    for (x, y) in body:
        sy = y + 1
        if (x, sy) not in body and 0 < sy < G - 1 and 0 < x < G - 1:
            px[sy][x] = FIG_LO

    for (x, y) in body:
        if 0 <= x < G and 0 <= y < G:
            px[y][x] = FIG

    return px


def main():
    px = build_grid()
    img = Image.new("RGBA", (G, G))
    for y in range(G):
        for x in range(G):
            img.putpixel((x, y), px[y][x])
    img = img.resize((G * SCALE, G * SCALE), Image.NEAREST)

    out = os.path.join(STATIC, "logo-channel.png")
    img.save(out)
    print(f"wrote {os.path.normpath(out)} ({img.width}x{img.height}, grid {G})")


if __name__ == "__main__":
    main()
