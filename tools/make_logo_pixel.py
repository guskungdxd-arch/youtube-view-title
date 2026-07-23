#!/usr/bin/env python3
"""Generate the PIXEL-ART ViewTitle logo — an 8-bit "67" cartridge to match the
retro site theme.  The smooth vector mark still lives in make_logo.py; this is
the alternate identity.

Pillow is in the system interpreter, not the venv:
    /usr/local/bin/python3 tools/make_logo_pixel.py

Everything is drawn on a 16x16 logical grid so the browser-tab favicon (16px)
is pixel-perfect 1:1 and the 512px master is an exact 32x upscale.  Writes:
    web/static/logo.png            120x120   (Google Console size)
    web/static/logo-512.png        512x512
    web/static/logo.svg            vector grid of <rect>s, crispEdges
    web/static/favicon.ico         16/32/48, crisp nearest
    web/static/apple-touch-icon.png 180x180, flattened on the plate colour
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "web", "static")
G = 16  # logical grid side

# palette — brand blue plate with a pixel bevel, amber arcade digits
PLATE = (0x00, 0x71, 0xE3, 255)
HI    = (0x53, 0xA6, 0xFF, 255)   # top/left bevel highlight
LO    = (0x00, 0x52, 0xBD, 255)   # bottom/right bevel shadow
DIGIT = (0xFF, 0xD2, 0x4A, 255)   # amber "67"
DSHAD = (0x00, 0x45, 0x93, 255)   # 1px drop shadow under the digits
CLEAR = (0, 0, 0, 0)

# 5x7 pixel numerals
GLYPH = {
    "6": [
        ".###.",
        "#....",
        "#....",
        "####.",
        "#...#",
        "#...#",
        ".###.",
    ],
    "7": [
        "#####",
        "....#",
        "...#.",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
    ],
}


def build_grid():
    """Return a 16x16 list of RGBA tuples."""
    px = [[PLATE for _ in range(G)] for _ in range(G)]

    # 1px bevel: highlight along top & left, shadow along bottom & right
    for i in range(G):
        px[0][i] = HI
        px[i][0] = HI
        px[G - 1][i] = LO
        px[i][G - 1] = LO

    # chamfer the four corners (1px) so it reads as a rounded pixel tile
    for (y, x) in [(0, 0), (0, G - 1), (G - 1, 0), (G - 1, G - 1)]:
        px[y][x] = CLEAR

    # place "6" and "7": each glyph 5 wide, 1px gap -> 11 wide, centred in 16
    x6, x7, y0 = 2, 9, 4
    for glyph, gx in (("6", x6), ("7", x7)):
        for gy, row in enumerate(GLYPH[glyph]):
            for dx, c in enumerate(row):
                if c == "#":
                    y, x = y0 + gy, gx + dx
                    # 1px drop shadow first (down-right), then the digit
                    if px[y + 1][x + 1] not in (HI,) and y + 1 < G - 1 and x + 1 < G - 1:
                        px[y + 1][x + 1] = DSHAD
    for glyph, gx in (("6", x6), ("7", x7)):
        for gy, row in enumerate(GLYPH[glyph]):
            for dx, c in enumerate(row):
                if c == "#":
                    px[y0 + gy][gx + dx] = DIGIT
    return px


def to_image(px, scale):
    img = Image.new("RGBA", (G, G))
    for y in range(G):
        for x in range(G):
            img.putpixel((x, y), px[y][x])
    if scale != 1:
        img = img.resize((G * scale, G * scale), Image.NEAREST)
    return img


def render(px, size):
    """Nearest-neighbour render at an arbitrary output size."""
    base = to_image(px, 1)
    return base.resize((size, size), Image.NEAREST)


def write_svg(px, path, size=512):
    cell = size / G
    runs = []
    for y in range(G):
        for x in range(G):
            r, g, b, a = px[y][x]
            if a == 0:
                continue
            runs.append(
                f'<rect x="{x*cell:g}" y="{y*cell:g}" width="{cell:g}" '
                f'height="{cell:g}" fill="#{r:02x}{g:02x}{b:02x}"/>'
            )
    body = "\n  ".join(runs)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" shape-rendering="crispEdges">\n  {body}\n</svg>\n'
    )
    with open(path, "w") as fh:
        fh.write(svg)


def main():
    px = build_grid()

    render(px, 120).save(os.path.join(STATIC, "logo.png"))
    render(px, 512).save(os.path.join(STATIC, "logo-512.png"))
    write_svg(px, os.path.join(STATIC, "logo.svg"))

    # favicon: 48px crisp-nearest base so Pillow can emit 16/32/48
    render(px, 48).save(os.path.join(STATIC, "favicon.ico"),
                        sizes=[(16, 16), (32, 32), (48, 48)])

    # apple-touch: flatten the 16-grid onto the plate colour (iOS masks corners)
    touch = Image.new("RGBA", (G, G), PLATE)
    touch.alpha_composite(to_image(px, 1))
    touch.convert("RGB").resize((180, 180), Image.NEAREST).save(
        os.path.join(STATIC, "apple-touch-icon.png"))

    for f in ("logo.png", "logo-512.png", "logo.svg", "favicon.ico",
              "apple-touch-icon.png"):
        print("wrote", os.path.normpath(os.path.join(STATIC, f)))


if __name__ == "__main__":
    main()
