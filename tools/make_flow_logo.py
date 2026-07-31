#!/usr/bin/env python3
"""โลโก้การ์ด Flow — ไอคอน "ท่อส่งข้อมูล" แบบ pixel art

Pillow อยู่ใน interpreter ของระบบ ไม่ใช่ venv ของโปรเจกต์:
    /usr/local/bin/python3 tools/make_flow_logo.py

กริด 16x16 + พื้น/bevel/มุมตัดชุดเดียวกับ make_logo_pixel.py ("67") และ
make_avatar_logo.py (Channel Stats) — สามการ์ดอยู่เรียงกันบนหน้าแรก
ถ้ากริดไม่เท่ากันเม็ดพิกเซลจะดูไม่เท่ากัน

ตัวมาร์ค: กล่องสองใบต่อกันด้วยแพ็กเก็ตที่วิ่งอยู่ระหว่างทาง (packet in transit)
สื่อถึงไดอะแกรมบนหน้า /flow ที่แพ็กเก็ตไหลจากกล่องบนลงกล่องล่าง

เขียนไฟล์: web/static/logo-flow.png (16*8 = 128px)
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
BOX = (0xF2, 0xEC, 0xD8, 255)     # กล่อง — ครีม (--surface ของ ARCADE DAY)
DOT = (0xFF, 0xD2, 0x4A, 255)     # แพ็กเก็ต — amber (--accent ของ NIGHT CRT)
CLEAR = (0, 0, 0, 0)

# '#' = กล่อง, '*' = แพ็กเก็ตระหว่างทาง, '.' = พื้น
# กล่องสองใบ (r2-3 / r11-12) วางสมมาตรรอบแถว 7 แพ็กเก็ตสองก้อนคั่นด้วยช่องว่าง
# — ระยะห่างต้องเห็นชัดว่าเป็น "ท่อ" ไม่ใช่ลิ้นชักติดกัน
FIGURE = [
    "................",
    "................",
    "...##########...",
    "...##########...",
    "................",
    ".......**.......",
    ".......**.......",
    "................",
    ".......**.......",
    ".......**.......",
    "................",
    "...##########...",
    "...##########...",
    "................",
    "................",
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

    boxes = {(x, y) for y, row in enumerate(FIGURE)
             for x, c in enumerate(row) if c == "#"}
    dots = {(x, y) for y, row in enumerate(FIGURE)
            for x, c in enumerate(row) if c == "*"}

    # ไม่มีเงาใต้กล่องเหมือนโลโก้ใบอื่น — เงาแถวเดียวใต้กล่องบนจะไปปิดช่องว่าง
    # ที่ทำให้ท่ออ่านออกว่าเป็นท่อ กลายเป็นตู้ลิ้นชักแทน
    for (x, y) in boxes:
        px[y][x] = BOX
    for (x, y) in dots:
        px[y][x] = DOT

    return px


def main():
    px = build_grid()
    img = Image.new("RGBA", (G, G))
    for y in range(G):
        for x in range(G):
            img.putpixel((x, y), px[y][x])
    img = img.resize((G * SCALE, G * SCALE), Image.NEAREST)

    out = os.path.join(STATIC, "logo-flow.png")
    img.save(out)
    print(f"wrote {os.path.normpath(out)} ({img.width}x{img.height}, grid {G})")


if __name__ == "__main__":
    main()
