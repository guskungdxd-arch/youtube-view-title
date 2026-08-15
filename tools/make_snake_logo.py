#!/usr/bin/env python3
"""โลโก้การ์ด Snake — ไอคอน "งูกับอาหาร" แบบ pixel art

Pillow อยู่ใน interpreter ของระบบ ไม่ใช่ venv ของโปรเจกต์:
    /usr/local/bin/python3 tools/make_snake_logo.py

กริด 16x16 + พื้น/bevel/มุมตัดชุดเดียวกับ make_logo_pixel.py ("67"),
make_avatar_logo.py (Channel Stats) และ make_flow_logo.py — สี่การ์ดอยู่เรียงกัน
บนหน้าแรก ถ้ากริดไม่เท่ากันเม็ดพิกเซลจะดูไม่เท่ากัน

ตัวมาร์ค: ลำตัวงูขดแบบ serpentine สามชั้น + อาหารหนึ่งก้อนรออยู่ทางขวา
สื่อถึงเกมบนหน้า /snake ตรง ๆ

เขียนไฟล์: web/static/logo-snake.png (16*8 = 128px)
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
BODY = (0xF2, 0xEC, 0xD8, 255)    # ลำตัวงู — ครีม (--surface ของ ARCADE DAY)
FOOD = (0xFF, 0xD2, 0x4A, 255)    # อาหาร — amber (--accent ของ NIGHT CRT)
CLEAR = (0, 0, 0, 0)

# '#' = ลำตัวงู, '*' = อาหาร, '.' = พื้น
# ลำตัวหนา 2px เลี้ยวมุมขึ้น หัวชี้ไปทางอาหารที่รออยู่ทางขวา
# เคยลองทำเป็นปล้องเว้นช่องตามที่เกมวาดจริงแล้ว — ย่อเหลือ 46px บนการ์ดหน้าแรก
# มันแตกเป็นจุดกระจาย อ่านไม่ออกว่าเป็นงู เส้นทึบหนาจึงจำเป็น
FIGURE = [
    "................",
    "................",
    "................",
    "................",
    ".........##.**..",
    ".........##.**..",
    ".........##.....",
    ".........##.....",
    ".........##.....",
    "...########.....",
    "...########.....",
    "................",
    "................",
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

    body = {(x, y) for y, row in enumerate(FIGURE)
            for x, c in enumerate(row) if c == "#"}
    food = {(x, y) for y, row in enumerate(FIGURE)
            for x, c in enumerate(row) if c == "*"}

    for (x, y) in body:
        px[y][x] = BODY
    for (x, y) in food:
        px[y][x] = FOOD

    return px


def main():
    px = build_grid()
    img = Image.new("RGBA", (G, G))
    for y in range(G):
        for x in range(G):
            img.putpixel((x, y), px[y][x])
    img = img.resize((G * SCALE, G * SCALE), Image.NEAREST)

    out = os.path.join(STATIC, "logo-snake.png")
    img.save(out)
    print(f"wrote {os.path.normpath(out)} ({img.width}x{img.height}, grid {G})")


if __name__ == "__main__":
    main()
