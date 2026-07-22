#!/usr/bin/env python3
"""Generate the ViewTitle logo mark (PNG + SVG).

The mark: a rounded blue square built like a split-flap counter card -- a
lighter upper flap, a deeper lower flap, a dark hinge line across the middle,
and two tabular numerals sitting across the hinge as if mid-tick.  The
numerals are drawn as stroked polylines (no font is used anywhere) so the PNG
and the SVG come out of the *same* geometry and the SVG is self-contained.

Requires Pillow.  Neither venv in this repo ships it; on the owner's Mac
the interpreter that has it is /usr/local/bin/python3:

    /usr/local/bin/python3 tools/make_logo.py            # writes web/static/
    /usr/local/bin/python3 tools/make_logo.py --preview  # + scratch previews

Outputs (overwritten in place, templates already reference these names):
    web/static/logo.png       120x120 RGBA  (size Google Cloud Console wants)
    web/static/logo-512.png   512x512 RGBA
    web/static/logo.svg       vector, geometry only
"""

from __future__ import annotations

import argparse
import math
import os

from PIL import Image, ImageChops, ImageDraw

# --------------------------------------------------------------------------
# brand
# --------------------------------------------------------------------------

BLUE = (0x00, 0x71, 0xE3)          # --blue in the site CSS
BLUE_DEEP = (0x00, 0x5F, 0xC8)     # lower flap of the split-flap card
BLUE_SEAM = (0x00, 0x45, 0x93)     # the hairline where the flap hinges
INK = (0xFF, 0xFF, 0xFF)           # the numerals

RADIUS = 0.2245                    # corner radius, as a fraction of the side
SS = 8                             # supersampling factor for the raster pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(REPO, "web", "static")


# --------------------------------------------------------------------------
# geometry helpers -- everything is a polyline in unit-cell space
# --------------------------------------------------------------------------

def arc(cx, cy, rx, ry, a0, a1, n=48):
    """Elliptical arc as points.  Angles in degrees, y grows downward, so
    270 = top, 90 = bottom, 0 = right, 180 = left."""
    return [
        (cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a)))
        for a in (a0 + (a1 - a0) * i / n for i in range(n + 1))
    ]


def _scale(paths, w, h):
    return [[(x * w, y * h) for (x, y) in p] for p in paths]


# Numeral skeletons, expressed on a 1x1 cell (centerlines; the stroke width
# is added around them).  Only the digits the mark actually needs exist here.
def digit(d, w=1.0, h=1.0):
    if d == "0":
        r = 0.5
        p = (
            arc(0.5, r, 0.5, r, 180, 360, 32)          # top half
            + [(1.0, 1.0 - r)]
            + arc(0.5, 1.0 - r, 0.5, r, 0, 180, 32)    # bottom half
            + [(0.0, r)]
        )
        return _scale([p], w, h)
    if d == "1":
        return _scale([[(0.16, 0.27), (0.56, 0.0), (0.56, 1.0)]], w, h)
    if d == "1f":  # tabular "1": the monospace foot serif
        return _scale(
            [[(0.16, 0.27), (0.56, 0.0), (0.56, 1.0)], [(0.10, 1.0), (1.0, 1.0)]],
            w, h,
        )
    if d == "2":
        # the shoulder needs a generous radius or a heavy stroke closes the counter
        p = arc(0.5, 0.35, 0.48, 0.35, 163, 377, 56)
        p += [(0.04, 1.0), (1.0, 1.0)]
        return _scale([p], w, h)
    if d == "3":
        p = arc(0.48, 0.26, 0.45, 0.26, 168, 428, 44)
        p += arc(0.45, 0.72, 0.50, 0.28, 292, 548, 44)
        return _scale([p], w, h)
    if d == "4":
        return _scale(
            [[(0.74, 0.0), (0.02, 0.69), (1.0, 0.69)], [(0.74, 0.30), (0.74, 1.0)]],
            w, h,
        )
    if d == "5":
        p = [(0.94, 0.02), (0.08, 0.02), (0.08, 0.42)]
        p += arc(0.47, 0.69, 0.47, 0.31, 219, 530, 44)
        return _scale([p], w, h)
    if d == "7":
        return _scale([[(0.03, 0.02), (0.97, 0.02), (0.36, 1.0)]], w, h)
    if d == "8":
        p = arc(0.5, 0.26, 0.44, 0.26, -90, 270, 48)
        p += arc(0.5, 0.73, 0.50, 0.27, 270, 630, 48)
        return _scale([p], w, h)
    if d == "9":
        p = arc(0.5, 0.29, 0.44, 0.29, 20, 380, 48)
        p += [(0.94, 0.42), (0.94, 0.80)]
        p += arc(0.54, 0.80, 0.40, 0.20, 0, 130, 24)
        return _scale([p], w, h)
    raise ValueError(f"no skeleton for {d!r}")


# --------------------------------------------------------------------------
# raster painting
# --------------------------------------------------------------------------

def stroke(draw, pts, width, fill, cap="round"):
    """Stroke a polyline the way SVG would: round joins, optional round caps.

    Pillow's own joint="curve" leaves hairline gaps between segment quads on
    tight curves (they survive the downsample as faint streaks), so each
    segment is drawn separately and every interior vertex gets a disc.
    """
    w = max(int(round(width)), 1)
    r = width / 2.0
    for a, b in zip(pts, pts[1:]):
        draw.line([a, b], fill=fill, width=w)
    verts = list(pts) if cap == "round" else list(pts[1:-1])
    for x, y in verts:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def plate(size, radius_frac=RADIUS, style="split"):
    """The rounded-square background, on transparency.

    style="split"  -> flip-clock card: lighter upper flap, deeper lower flap
    style="grad"   -> subtle vertical gradient
    style="flat"   -> one flat blue
    """
    if style == "grad":
        body = Image.new("RGBA", (1, size))
        for y in range(size):
            t = y / max(size - 1, 1)
            body.putpixel(
                (0, y),
                tuple(int(round(a + (b - a) * t)) for a, b in zip(BLUE, BLUE_DEEP)) + (255,),
            )
        body = body.resize((size, size))
    elif style == "split":
        body = Image.new("RGBA", (size, size), BLUE + (255,))
        ImageDraw.Draw(body).rectangle((0, size // 2, size, size), fill=BLUE_DEEP + (255,))
    else:
        body = Image.new("RGBA", (size, size), BLUE + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=size * radius_frac, fill=255
    )
    # Set alpha rather than compositing onto transparent black: the RGB under
    # the transparent corners stays blue, so downsampling cannot bleed a dark
    # fringe into the rounded corners.
    body.putalpha(mask)
    return body


def draw_readout(draw, S, digits, weight=0.108, cap_h=0.50, gap=0.085,
                 cap="round"):
    """Lay out `digits` as one tabular block, centred on the plate.

    Returns (polylines, stroke_width) so the SVG writer can replay exactly the
    same geometry that was rasterised.
    """
    ops = []
    ch = cap_h * S                     # cap height
    cw = 0.60 * ch                     # tabular advance (centerline box width)
    sw = weight * S                    # stroke weight
    g = gap * S
    n = len(digits)
    total = n * cw + (n - 1) * g
    x0 = (S - total) / 2.0
    y0 = (S - ch) / 2.0

    for i, d in enumerate(digits):
        cx = x0 + i * (cw + g)
        for p in digit(d, cw, ch):
            ops.append(("stroke", [(cx + x, y0 + y) for (x, y) in p]))

    # Optical centring: a tabular "1" carries its ink to the right of its cell,
    # so centring the metric box leaves the pair looking shifted.  Centre the
    # ink instead.
    xs = [p[0] for _, pts in ops for p in pts]
    ys = [p[1] for _, pts in ops for p in pts]
    dx = S / 2.0 - (min(xs) + max(xs)) / 2.0
    dy = S / 2.0 - (min(ys) + max(ys)) / 2.0
    ops = [(k, [(x + dx, y + dy) for x, y in pts]) for k, pts in ops]

    for kind, pts in ops:
        stroke(draw, pts, sw, INK, cap=cap)
    return ops, sw


# --------------------------------------------------------------------------
# variants (kept so the mark can be re-explored without starting over)
# --------------------------------------------------------------------------

def _base(**kw):
    v = dict(digits=("1", "2"), weight=0.115, cap_h=0.48, gap=0.10,
             plate="split", seam=0.016, cap="round")
    v.update(kw)
    return v


VARIANTS = {
    # THE SHIPPED MARK.  Cap height and weight were pushed up until the pair
    # still read at 22px; the seam is only 1.6% of the side, so it reads as a
    # flip-clock hinge from ~40px up and resolves away at nav size instead of
    # chewing a hole in the digits.
    "final":     _base(),
    # explored and rejected, kept as starting points if the mark is revisited
    "final-air": _base(cap_h=0.45, weight=0.112, gap=0.105),  # airier, weaker at 22px
    "flat":      _base(plate="flat", seam=0),                 # no flip-card cue
    "grad":      _base(plate="grad", seam=0),
    "seam-behind": _base(seam=-0.016),      # hinge passes behind the numerals
    "foot":      _base(digits=("1f", "2")),  # monospace foot on the 1
    "zero-nine": _base(digits=("0", "9")),   # counters close up at 22px
}

DEFAULT_VARIANT = "final"


def seam_line(img, S, thickness, radius_frac=RADIUS):
    """The hinge line of the split-flap card, clipped to the plate."""
    if not thickness:
        return
    t = abs(thickness) * S
    band = Image.new("L", (S, S), 0)
    ImageDraw.Draw(band).rectangle((0, S / 2 - t / 2, S, S / 2 + t / 2), fill=255)
    plate_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(plate_mask).rounded_rectangle(
        (0, 0, S - 1, S - 1), radius=S * radius_frac, fill=255)
    img.paste(Image.new("RGBA", (S, S), BLUE_SEAM + (255,)), (0, 0),
              ImageChops.multiply(band, plate_mask))


def render_png(size, variant="split12"):
    v = VARIANTS[variant]
    big = size * SS
    img = plate(big, style=v["plate"])
    draw = ImageDraw.Draw(img)
    if v["seam"] < 0:                      # seam sits behind the numerals
        seam_line(img, big, v["seam"])
    draw_readout(draw, big, v["digits"], weight=v["weight"],
                 cap_h=v["cap_h"], gap=v["gap"], cap=v["cap"])
    if v["seam"] > 0:                      # seam cuts through the numerals
        seam_line(img, big, v["seam"])
    return img.resize((size, size), Image.LANCZOS)


def render_svg(size=512, variant="split12"):
    v = VARIANTS[variant]
    scratch = Image.new("RGBA", (8, 8))
    ops, sw = draw_readout(
        ImageDraw.Draw(scratch), size, v["digits"],
        weight=v["weight"], cap_h=v["cap_h"], gap=v["gap"], cap=v["cap"]
    )
    paths = "\n".join(
        '    <path d="M {}"/>'.format(
            " L ".join(f"{x:.2f} {y:.2f}" for x, y in pts)
        )
        for _, pts in ops
    )
    r = size * RADIUS
    hexa = "#%02x%02x%02x" % BLUE
    hexb = "#%02x%02x%02x" % BLUE_DEEP
    hexs = "#%02x%02x%02x" % BLUE_SEAM
    seam_t = abs(v["seam"]) * size
    defs = (
        '  <defs>\n'
        '    <clipPath id="vt-card">\n'
        f'      <rect width="{size}" height="{size}" rx="{r:.2f}" ry="{r:.2f}"/>\n'
        '    </clipPath>\n'
    )
    if v["plate"] == "grad":
        defs += (
            '    <linearGradient id="vt-blue" x1="0" y1="0" x2="0" y2="1">\n'
            f'      <stop offset="0" stop-color="{hexa}"/>\n'
            f'      <stop offset="1" stop-color="{hexb}"/>\n'
            '    </linearGradient>\n'
        )
        card = f'    <rect width="{size}" height="{size}" fill="url(#vt-blue)"/>\n'
    elif v["plate"] == "split":
        card = (
            f'    <rect width="{size}" height="{size}" fill="{hexa}"/>\n'
            f'    <rect y="{size / 2:.2f}" width="{size}" height="{size / 2:.2f}"'
            f' fill="{hexb}"/>\n'
        )
    else:
        card = f'    <rect width="{size}" height="{size}" fill="{hexa}"/>\n'
    defs += '  </defs>\n'

    seam = ""
    if seam_t:
        seam = (
            f'    <rect y="{size / 2 - seam_t / 2:.2f}" width="{size}"'
            f' height="{seam_t:.2f}" fill="{hexs}"/>\n'
        )
    numerals = (
        f'    <g fill="none" stroke="#ffffff" stroke-width="{sw:.2f}"'
        f' stroke-linecap="{v["cap"]}" stroke-linejoin="round">\n'
        f'{paths}\n'
        '    </g>\n'
    )
    body = card + (seam + numerals if v["seam"] < 0 else numerals + seam)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"'
        f' width="{size}" height="{size}" role="img" aria-label="ViewTitle">\n'
        '  <title>ViewTitle</title>\n'
        f'{defs}'
        '  <g clip-path="url(#vt-card)">\n'
        f'{body}'
        '  </g>\n'
        '</svg>\n'
    )


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default=DEFAULT_VARIANT, choices=sorted(VARIANTS))
    ap.add_argument("--out-dir", default=STATIC)
    ap.add_argument("--prefix", default="logo")
    ap.add_argument("--preview", metavar="DIR", help="also write 22/44/64px previews")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    for size, name in ((120, f"{args.prefix}.png"), (512, f"{args.prefix}-512.png")):
        img = render_png(size, args.variant).convert("RGBA")
        img.save(os.path.join(args.out_dir, name))
        print("wrote", os.path.join(args.out_dir, name), img.size, img.mode)

    svg_path = os.path.join(args.out_dir, f"{args.prefix}.svg")
    with open(svg_path, "w") as fh:
        fh.write(render_svg(512, args.variant))
    print("wrote", svg_path)

    if args.preview:
        os.makedirs(args.preview, exist_ok=True)
        master = render_png(512, args.variant)
        for s in (22, 44, 64, 88):
            master.resize((s, s), Image.LANCZOS).save(
                os.path.join(args.preview, f"{args.prefix}-{args.variant}-{s}.png")
            )
        print("previews ->", args.preview)


if __name__ == "__main__":
    main()
