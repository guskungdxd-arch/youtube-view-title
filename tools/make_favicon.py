#!/usr/bin/env python3
"""Generate favicon assets from the ViewTitle logo.

Pillow lives in the system interpreter here, not the project venv:
    /usr/local/bin/python3 tools/make_favicon.py

Reads web/static/logo-512.png (the split-flap counter mark) and writes:
    web/static/favicon.ico          multi-size 16/32/48, transparent (browser tab)
    web/static/apple-touch-icon.png 180x180, flattened on white (iOS home screen —
                                    iOS composites transparency onto black, so we
                                    flatten to avoid dark corners)
"""
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "web", "static")
SRC = os.path.join(STATIC, "logo-512.png")


def main():
    src = Image.open(SRC).convert("RGBA")

    # favicon.ico — keep transparency, browsers render it on the tab bar.
    ico_path = os.path.join(STATIC, "favicon.ico")
    src.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])

    # apple-touch-icon — flatten onto white; iOS masks its own rounded corners.
    touch = Image.new("RGBA", src.size, (255, 255, 255, 255))
    touch.alpha_composite(src)
    touch = touch.convert("RGB").resize((180, 180), Image.LANCZOS)
    touch_path = os.path.join(STATIC, "apple-touch-icon.png")
    touch.save(touch_path, format="PNG")

    print("wrote", os.path.normpath(ico_path))
    print("wrote", os.path.normpath(touch_path))


if __name__ == "__main__":
    main()
