#!/usr/bin/env python3
"""สร้างรูป OG (social preview) — ท้องฟ้าพระอาทิตย์ตกแบบ pixel art

Pillow อยู่ใน interpreter ของระบบ ไม่ใช่ venv ของโปรเจกต์:
    /usr/local/bin/python3 tools/make_og_image.py

วาดบนกริด 120x63 แล้วขยาย NEAREST 10 เท่า -> 1200x630 (ขนาดมาตรฐาน OG)
อัตราส่วนลงตัวพอดี พิกเซลจึงคมเป๊ะ ไม่มีขอบเบลอ

สีไล่ใช้ ordered dithering (Bayer 4x4) แบบเดียวกับที่เครื่องเกมยุค 8/16-bit ทำ
เพราะจอสมัยนั้นมีสีจำกัด ต้องเกลี่ยด้วยลายจุด ไม่ใช่ gradient เนียน
พาเลตต์ยืมสีจริงของเว็บ: cobalt #2b45c4 กับ hot #d61f4e (ARCADE DAY)
และ amber #ffd24a (NIGHT CRT)
"""
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "web", "static")

GW, GH = 120, 63          # กริดตรรกะ
SCALE = 10                # 120*10 x 63*10 = 1200x630
HORIZON = 52              # แถวที่ฟ้าจบ ใต้จากนี้เป็นเงาภูเขา

# ไล่สีฟ้าจากบนลงล่าง
SKY = [
    (0x0a, 0x10, 0x30),
    (0x1b, 0x2a, 0x7a),
    (0x2b, 0x45, 0xc4),   # cobalt — สี accent ของเว็บ
    (0x7a, 0x3b, 0xa8),
    (0xd6, 0x1f, 0x4e),   # hot — สี accent ของเว็บ
    (0xff, 0x7a, 0x3c),
    (0xff, 0xb0, 0x3c),
    (0xff, 0xd2, 0x4a),   # amber — สี accent ของเว็บ
]
CLOUD = (0xf2, 0xec, 0xd8)        # ครีม (--surface ของ ARCADE DAY)
CLOUD_LO = (0xc9, 0x7d, 0x92)     # ใต้ก้อนเมฆ อมชมพูให้เข้ากับฟ้าตอนเย็น
SUN = (0xff, 0xf3, 0xd0)
SUN_LO = (0xff, 0xd2, 0x4a)
HILL_FAR = (0x3a, 0x1f, 0x4a)
HILL_NEAR = (0x14, 0x0d, 0x22)
STAR = (0xf2, 0xec, 0xd8)

BAYER = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]


def bayer(x, y):
    return BAYER[y % 4][x % 4] / 16.0


def sky_colour(x, y):
    """สีฟ้าที่ (x,y) พร้อม ordered dithering ระหว่างสองสีที่ใกล้ที่สุด"""
    t = (y / max(HORIZON - 1, 1)) * (len(SKY) - 1)
    i = min(int(t), len(SKY) - 2)
    frac = t - i
    return SKY[i + 1] if bayer(x, y) < frac else SKY[i]


def draw_sun(px, cx, cy, r):
    """ดวงอาทิตย์ + ร่องขวางแบบ retro (ยิ่งลงล่างร่องยิ่งถี่)"""
    for y in range(cy - r, cy + r + 1):
        if not (0 <= y < HORIZON):
            continue
        for x in range(cx - r, cx + r + 1):
            if not (0 <= x < GW):
                continue
            dx, dy = x - cx, y - cy
            if dx * dx + dy * dy > r * r:
                continue
            # ร่อง: ใต้กลางดวงเริ่มมีเส้นเว้นถี่ขึ้นเรื่อย ๆ
            rel = (y - (cy - r)) / (2 * r)
            if rel > 0.32:
                period = max(2, int(6 - rel * 4))
                if (y - cy) % period == 0:
                    continue
            # ไล่สีในดวงเอง
            px[y][x] = SUN if rel < 0.55 else SUN_LO


def draw_cloud(px, cx, base_y, w):
    """ก้อนเมฆแบบ pixel: วงกลมซ้อนกันหลายก้อน ก้นแบน (เมฆจริงก้นแบน)

    ใช้วงกลมไม่ใช่สี่เหลี่ยม — สี่เหลี่ยมซ้อนกันออกมาเป็นแผ่นแบน ไม่เป็นก้อนปุย
    """
    h = max(3.0, w / 4.0)
    lobes = [
        (cx - w * 0.50, base_y - h * 0.30, h * 0.62),
        (cx - w * 0.28, base_y - h * 0.55, h * 0.92),
        (cx + w * 0.02, base_y - h * 0.95, h * 1.25),
        (cx + w * 0.30, base_y - h * 0.58, h * 0.98),
        (cx + w * 0.52, base_y - h * 0.28, h * 0.66),
    ]
    body = set()
    for lx, ly, r in lobes:
        r2 = r * r
        for y in range(int(ly - r) - 1, int(ly + r) + 2):
            for x in range(int(lx - r) - 1, int(lx + r) + 2):
                if 0 <= x < GW and 0 <= y < HORIZON and y <= base_y:
                    if (x - lx) ** 2 + (y - ly) ** 2 <= r2:
                        body.add((x, y))
    for (x, y) in body:
        px[y][x] = CLOUD
    # ขอบล่างของก้อน ทำเงาอมชมพู 1 พิกเซล
    for (x, y) in body:
        if (x, y + 1) not in body and y + 1 < HORIZON:
            px[y][x] = CLOUD_LO


def draw_hills(px):
    """เงาภูเขาด้านล่าง ให้ภาพมีพื้น ไม่ลอยเป็นแค่ไล่สี"""
    import math
    for x in range(GW):
        far = HORIZON + 1 + int(2.5 * math.sin(x / 13.0) + 1.5 * math.sin(x / 5.0))
        for y in range(max(far, HORIZON), GH):
            px[y][x] = HILL_FAR
    for x in range(GW):
        near = HORIZON + 5 + int(3.0 * math.sin(x / 9.0 + 2.0))
        for y in range(max(near, HORIZON), GH):
            px[y][x] = HILL_NEAR


def main():
    px = [[sky_colour(x, y) for x in range(GW)] for y in range(GH)]

    # ดาว: เฉพาะแถบบนที่ยังมืดพอ
    for (sx, sy) in [(9, 4), (23, 8), (38, 3), (52, 9), (71, 5),
                     (88, 8), (103, 4), (113, 10), (31, 13), (96, 14)]:
        if sy < HORIZON:
            px[sy][sx] = STAR

    draw_sun(px, cx=60, cy=42, r=13)

    draw_cloud(px, cx=22, base_y=22, w=26)
    draw_cloud(px, cx=95, base_y=17, w=30)
    # เว้นระยะจากดวงอาทิตย์ (x 47-73) ไม่ให้เบียดกัน
    draw_cloud(px, cx=27, base_y=34, w=15)
    draw_cloud(px, cx=98, base_y=39, w=18)

    draw_hills(px)

    img = Image.new("RGB", (GW, GH))
    for y in range(GH):
        for x in range(GW):
            img.putpixel((x, y), px[y][x])
    img = img.resize((GW * SCALE, GH * SCALE), Image.NEAREST)

    out = os.path.join(STATIC, "og.png")
    img.save(out)
    print(f"wrote {os.path.normpath(out)} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
