#!/usr/bin/env python3
"""Build the original Blueprint outline icon family (build dependency: Pillow).

All artwork uses a 64-unit canvas. SVG and transparent PNG assets share the same
vector commands; the application itself has no Pillow dependency.
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


KINDS = ("general", "chat", "web", "finance", "document", "calendar", "automation", "research")
OUTLINE = "#343A40"
ACCENT = "#647F89"
SIZE = 64


def rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


class Canvas:
    """Small dual SVG/Pillow stroke renderer with supersampled raster output."""

    def __init__(self, scale: int = 16):
        self.scale = scale
        self.image = Image.new("RGBA", (SIZE * scale, SIZE * scale))
        self.parts: list[str] = []

    def path(self, commands, color=ACCENT, width=1.75):
        """Draw M/L/Q/C/Z commands without fills, with round caps and joins."""
        data = []
        subpaths = []
        vertices = []
        current = (0, 0)
        for command in commands:
            op, *values = command
            data.append(op + " ".join(str(value) for value in values))
            if op == "M":
                if vertices:
                    subpaths.append(vertices)
                current = tuple(values)
                vertices = [current]
            elif op == "L":
                current = tuple(values)
                vertices.append(current)
            elif op == "Q":
                first, control, last = current, values[:2], values[2:]
                for step in range(1, 25):
                    t = step / 24
                    vertices.append(tuple((1-t)**2*first[j] + 2*(1-t)*t*control[j] + t*t*last[j] for j in (0, 1)))
                current = tuple(last)
            elif op == "C":
                first, control1, control2, last = current, values[:2], values[2:4], values[4:]
                for step in range(1, 33):
                    t = step / 32
                    vertices.append(tuple((1-t)**3*first[j] + 3*(1-t)**2*t*control1[j] + 3*(1-t)*t*t*control2[j] + t**3*last[j] for j in (0, 1)))
                current = tuple(last)
            elif op == "Z" and vertices:
                vertices.append(vertices[0])
                current = vertices[0]
        if vertices:
            subpaths.append(vertices)
        self.parts.append(
            f'<path d="{" ".join(data)}" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        painter = ImageDraw.Draw(self.image)
        stroke = rgb(color) + (255,)
        radius = width * self.scale / 2
        for vertices in subpaths:
            points = [(x * self.scale, y * self.scale) for x, y in vertices]
            painter.line(points, fill=stroke, width=round(width * self.scale), joint="curve")
            for x, y in points:
                painter.ellipse((x-radius, y-radius, x+radius, y+radius), fill=stroke)

    def line(self, points, color=ACCENT, width=1.75):
        self.path([("M", *points[0]), *(("L", *point) for point in points[1:])], color, width)

    def rounded(self, box, radius=2.5, color=ACCENT, width=1.75):
        x0, y0, x1, y1 = box
        self.path([
            ("M", x0+radius, y0), ("L", x1-radius, y0),
            ("Q", x1, y0, x1, y0+radius), ("L", x1, y1-radius),
            ("Q", x1, y1, x1-radius, y1), ("L", x0+radius, y1),
            ("Q", x0, y1, x0, y1-radius), ("L", x0, y0+radius),
            ("Q", x0, y0, x0+radius, y0), ("Z",),
        ], color, width)

    def ellipse(self, box, color=ACCENT, width=1.75):
        x0, y0, x1, y1 = box
        cx, cy = (x0+x1)/2, (y0+y1)/2
        rx, ry = (x1-x0)/2, (y1-y0)/2
        k = 0.55228475
        self.path([
            ("M", cx+rx, cy),
            ("C", cx+rx, cy+k*ry, cx+k*rx, cy+ry, cx, cy+ry),
            ("C", cx-k*rx, cy+ry, cx-rx, cy+k*ry, cx-rx, cy),
            ("C", cx-rx, cy-k*ry, cx-k*rx, cy-ry, cx, cy-ry),
            ("C", cx+k*rx, cy-ry, cx+rx, cy-k*ry, cx+rx, cy), ("Z",),
        ], color, width)

    def svg(self, title: str) -> str:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64" role="img">'
            f'<title>Blueprint folder: {html.escape(title)}</title>'
            + ''.join(self.parts) + '</svg>\n'
        )


def draw_base(c: Canvas) -> None:
    # A single unfilled contour, shared by every category.
    c.path([
        ("M", 8, 20), ("L", 8, 18), ("Q", 8, 14, 12, 14),
        ("L", 23, 14), ("Q", 24.5, 14, 25.5, 15),
        ("L", 30, 20), ("L", 52, 20), ("Q", 56, 20, 56, 24),
        ("L", 56, 48), ("Q", 56, 52, 52, 52),
        ("L", 12, 52), ("Q", 8, 52, 8, 48), ("Z",),
    ], OUTLINE, 2.1)


def draw_symbol(c: Canvas, kind: str) -> None:
    # General intentionally stays blank: a new folder has no assigned purpose.
    if kind == "chat":
        c.path([
            ("M", 25, 28), ("L", 39, 28), ("Q", 42, 28, 42, 31),
            ("L", 42, 39), ("Q", 42, 42, 39, 42), ("L", 30, 42),
            ("L", 25, 46), ("L", 25, 42), ("Q", 22, 42, 22, 39),
            ("L", 22, 31), ("Q", 22, 28, 25, 28), ("Z",),
        ])
        c.line([(27, 34), (37, 34)], width=1.6)
        c.line([(27, 38), (33, 38)], width=1.6)
    elif kind == "web":
        c.ellipse((23, 27, 41, 45))
        c.ellipse((28, 27, 36, 45), width=1.4)
        c.line([(23, 36), (41, 36)], width=1.4)
    elif kind == "finance":
        c.line([(23, 28), (23, 44), (42, 44)])
        c.line([(27, 37), (32, 32), (36, 35), (42, 28)])
        c.line([(37, 28), (42, 28), (42, 33)])
    elif kind == "document":
        c.path([
            ("M", 27, 26), ("L", 35, 26), ("L", 41, 32),
            ("L", 41, 44), ("Q", 41, 46, 39, 46),
            ("L", 27, 46), ("Q", 25, 46, 25, 44),
            ("L", 25, 28), ("Q", 25, 26, 27, 26), ("Z",),
        ])
        c.line([(35, 26), (35, 32), (41, 32)], width=1.5)
        c.line([(29, 37), (37, 37)], width=1.5)
        c.line([(29, 41), (35, 41)], width=1.5)
    elif kind == "calendar":
        c.rounded((22, 29, 42, 46))
        c.line([(22, 34), (42, 34)], width=1.5)
        c.line([(27, 26), (27, 31)])
        c.line([(37, 26), (37, 31)])
        c.line([(28, 40), (31, 43), (36, 38)])
    elif kind == "automation":
        c.line([(28, 31), (32, 31), (32, 43), (36, 43)], width=1.5)
        c.line([(32, 31), (36, 31)], width=1.5)
        c.rounded((22, 28, 28, 34), 1.5, width=1.6)
        c.rounded((36, 28, 42, 34), 1.5, width=1.6)
        c.rounded((36, 40, 42, 46), 1.5, width=1.6)
    elif kind == "research":
        c.ellipse((23, 27, 37, 41))
        c.line([(35, 39), (42, 46)], width=2)


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    previews = []
    for kind in KINDS:
        c = Canvas()
        draw_base(c)
        draw_symbol(c, kind)
        (output / f"{kind}.svg").write_text(c.svg(kind), encoding="utf-8")
        raster = c.image.resize((512, 512), Image.Resampling.LANCZOS)
        raster.save(output / f"{kind}.png", optimize=True)
        raster.resize((256, 256), Image.Resampling.LANCZOS).save(output / f"{kind}-256.png", optimize=True)
        previews.append(raster)

    sheet = Image.new("RGB", (1200, 660), "#F6F7F8")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 16)
        heading_font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        font = heading_font = ImageFont.load_default()
    draw.text((36, 24), "BLUEPRINT / OUTLINE FOLDERS", fill=OUTLINE, font=heading_font)
    for i, (kind, preview) in enumerate(zip(KINDS, previews)):
        x = 24 + (i % 4) * 294
        y = 80 + (i // 4) * 278
        draw.rounded_rectangle((x, y, x+270, y+252), 14, fill="white")
        thumbnail = preview.resize((208, 208), Image.Resampling.LANCZOS)
        sheet.paste(thumbnail, (x+31, y+2), thumbnail)
        draw.text((x+135, y+222), kind.upper(), fill=OUTLINE, font=font, anchor="mm")
    sheet.save(output / "contact-sheet.png", optimize=True)
    print(f"Built {len(KINDS)} outline icon families and contact-sheet.png in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "assets" / "icons")
    args = parser.parse_args()
    build(args.output)
