#!/usr/bin/env python3
"""Rebuild the Blueprint folder icon family (build dependency: Pillow).

SVGs and raster assets share the same drawing commands. The application itself
does not import Pillow. Run: python tools/build_icons.py
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


KINDS = ("general", "chat", "web", "finance", "document", "calendar", "automation", "research")
NAVY = "#122743"
CYAN = "#52ead9"
WHITE = "#f2fbff"
MUTED = "#8cb0cc"
GRADIENTS = {
    "body": ("#1b3e60", "#10223b"),
    "tab": ("#3c8198", "#205169"),
    "accent": ("#8bfff0", "#33bfae"),
}


def rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


class Canvas:
    """Small dual SVG/Pillow vector renderer, with supersampled raster output."""

    def __init__(self, scale: int = 3):
        self.scale = scale
        self.image = Image.new("RGBA", (512 * scale, 512 * scale))
        self.parts: list[str] = []
        self.gradient_images: dict[str, Image.Image] = {}

    def _paint(self, mask: Image.Image, fill: str) -> None:
        if fill.startswith("url(#"):
            name = fill[5:-1]
            if name not in self.gradient_images:
                first, last = map(rgb, GRADIENTS[name])
                layer = Image.new("RGBA", self.image.size)
                painter = ImageDraw.Draw(layer)
                for y in range(layer.height):
                    fraction = min(1, max(0, (y / self.scale - 60) / 390))
                    color = tuple(round(a + (b - a) * fraction) for a, b in zip(first, last))
                    painter.line((0, y, layer.width, y), fill=color + (255,))
                self.gradient_images[name] = layer
            self.image.paste(self.gradient_images[name], (0, 0), mask)
        else:
            self.image.paste(rgb(fill) + (255,), (0, 0), mask)

    def _mask(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        mask = Image.new("L", self.image.size)
        return mask, ImageDraw.Draw(mask)

    def rounded(self, box, radius, fill: str, stroke: str | None = None, width: float = 1):
        x0, y0, x1, y1 = box
        self.parts.append(
            f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" rx="{radius}" '
            f'fill="{fill}" stroke="{stroke or "none"}" stroke-width="{width}"/>'
        )
        mask, draw = self._mask()
        draw.rounded_rectangle(tuple(v * self.scale for v in box), radius * self.scale, fill=255)
        self._paint(mask, fill)
        if stroke:
            mask, draw = self._mask()
            draw.rounded_rectangle(tuple(v * self.scale for v in box), radius * self.scale, outline=255, width=max(1, round(width * self.scale)))
            self._paint(mask, stroke)

    def ellipse(self, box, fill: str | None = None, stroke: str | None = None, width: float = 1):
        x0, y0, x1, y1 = box
        self.parts.append(
            f'<ellipse cx="{(x0+x1)/2}" cy="{(y0+y1)/2}" rx="{(x1-x0)/2}" ry="{(y1-y0)/2}" '
            f'fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width}"/>'
        )
        if fill:
            mask, draw = self._mask()
            draw.ellipse(tuple(v * self.scale for v in box), fill=255)
            self._paint(mask, fill)
        if stroke:
            mask, draw = self._mask()
            draw.ellipse(tuple(v * self.scale for v in box), outline=255, width=max(1, round(width * self.scale)))
            self._paint(mask, stroke)

    def line(self, points, color=WHITE, width=12):
        self.parts.append(
            '<polyline points="' + " ".join(f"{x},{y}" for x, y in points)
            + f'" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        mask, draw = self._mask()
        scaled = [(x * self.scale, y * self.scale) for x, y in points]
        draw.line(scaled, fill=255, width=round(width * self.scale), joint="curve")
        r = width * self.scale / 2
        for x, y in scaled:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
        self._paint(mask, color)

    def path(self, commands, fill: str, stroke: str | None = None, width: float = 1):
        """Support M/L/Q/C/Z, sampling curves only in the raster rendition."""
        d: list[str] = []
        vertices = []
        current = (0, 0)
        for command in commands:
            op, *values = command
            d.append(op + " ".join(str(v) for v in values))
            if op in ("M", "L"):
                current = tuple(values)
                vertices.append(current)
            elif op == "Q":
                p0, p1, p2 = current, values[:2], values[2:]
                for i in range(1, 25):
                    t = i / 24
                    vertices.append(tuple((1-t)**2*p0[j] + 2*(1-t)*t*p1[j] + t*t*p2[j] for j in (0, 1)))
                current = tuple(p2)
            elif op == "C":
                p0, p1, p2, p3 = current, values[:2], values[2:4], values[4:]
                for i in range(1, 33):
                    t = i / 32
                    vertices.append(tuple((1-t)**3*p0[j] + 3*(1-t)**2*t*p1[j] + 3*(1-t)*t*t*p2[j] + t**3*p3[j] for j in (0, 1)))
                current = tuple(p3)
        self.parts.append(f'<path d="{" ".join(d)}" fill="{fill}" stroke="{stroke or "none"}" stroke-width="{width}" stroke-linejoin="round"/>')
        mask, draw = self._mask()
        scaled = [(x * self.scale, y * self.scale) for x, y in vertices]
        draw.polygon(scaled, fill=255)
        self._paint(mask, fill)
        if stroke:
            mask, draw = self._mask()
            draw.line(scaled + [scaled[0]], fill=255, width=round(width * self.scale), joint="curve")
            self._paint(mask, stroke)

    def svg(self, title: str) -> str:
        defs = ''.join(
            f'<linearGradient id="{name}" x1="0" y1="60" x2="0" y2="450" gradientUnits="userSpaceOnUse">'
            f'<stop stop-color="{colors[0]}"/><stop offset="1" stop-color="{colors[1]}"/></linearGradient>'
            for name, colors in GRADIENTS.items()
        )
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512" role="img">'
            f'<title>Blueprint folder: {html.escape(title)}</title><defs>{defs}</defs>'
            + ''.join(self.parts) + '</svg>\n'
        )


def draw_base(c: Canvas) -> None:
    c.path([
        ("M", 58, 153), ("L", 58, 108), ("Q", 58, 77, 89, 77),
        ("L", 179, 77), ("Q", 192, 77, 203, 91), ("L", 228, 121),
        ("L", 420, 121), ("Q", 454, 121, 454, 153), ("L", 454, 202),
        ("L", 58, 202), ("Z",),
    ], "url(#tab)")
    c.line([(86, 108), (169, 108)], CYAN, 7)
    c.rounded((38, 143, 474, 442), 48, "url(#body)", "#345d78", 3)
    c.line([(82, 165), (426, 165)], "#58d9cf", 3)
    # Shared corner details keep the family recognizable without using text.
    c.rounded((396, 184, 429, 202), 7, "url(#accent)")
    c.line([(80, 410), (110, 410)], "#4c7e96", 4)
    c.ellipse((121, 406, 129, 414), "#4c7e96")


def draw_symbol(c: Canvas, kind: str) -> None:
    if kind == "general":
        for box in ((167, 217, 238, 282), (266, 217, 337, 282), (167, 308, 238, 373)):
            c.rounded(box, 15, "#244963", WHITE, 6)
        c.rounded((266, 308, 337, 373), 15, "url(#accent)")
        c.line([(287, 340), (316, 340)], NAVY, 7)
        c.line([(302, 326), (302, 354)], NAVY, 7)
    elif kind == "chat":
        c.path([
            ("M", 259, 270), ("L", 337, 270), ("Q", 365, 270, 365, 297),
            ("L", 365, 331), ("Q", 365, 355, 341, 355), ("L", 339, 377),
            ("L", 309, 355), ("L", 259, 355), ("Q", 235, 355, 235, 331),
            ("L", 235, 296), ("Q", 235, 270, 259, 270), ("Z",),
        ], "url(#accent)")
        c.path([
            ("M", 168, 218), ("L", 287, 218), ("Q", 316, 218, 316, 247),
            ("L", 316, 288), ("Q", 316, 317, 287, 317), ("L", 223, 317),
            ("L", 188, 345), ("L", 188, 317), ("L", 168, 317),
            ("Q", 139, 317, 139, 288), ("L", 139, 247), ("Q", 139, 218, 168, 218), ("Z",),
        ], "#173651", WHITE, 8)
        for x in (183, 227, 271):
            c.ellipse((x-6, 263, x+6, 275), CYAN)
    elif kind == "web":
        c.rounded((144, 211, 368, 374), 20, "#173651", WHITE, 8)
        c.line([(148, 249), (364, 249)], WHITE, 7)
        for x in (165, 183, 201):
            c.ellipse((x-3, 228, x+3, 234), CYAN)
        c.ellipse((211, 268, 301, 358), stroke=CYAN, width=7)
        c.ellipse((238, 268, 274, 358), stroke=CYAN, width=5)
        c.line([(213, 313), (299, 313)], CYAN, 5)
    elif kind == "finance":
        c.line([(151, 231), (151, 370), (366, 370)], WHITE, 8)
        c.rounded((179, 310, 208, 348), 6, WHITE)
        c.rounded((235, 280, 264, 348), 6, WHITE)
        c.rounded((291, 244, 320, 348), 6, "url(#accent)")
        c.line([(179, 273), (231, 242), (270, 254), (341, 212)], CYAN, 10)
        c.line([(313, 211), (342, 211), (342, 239)], CYAN, 9)
    elif kind == "document":
        c.path([
            ("M", 194, 207), ("L", 282, 207), ("L", 329, 255),
            ("L", 329, 355), ("Q", 329, 378, 306, 378), ("L", 194, 378),
            ("Q", 171, 378, 171, 355), ("L", 171, 230), ("Q", 171, 207, 194, 207), ("Z",),
        ], "#173651", WHITE, 8)
        c.path([("M", 282, 209), ("L", 282, 254), ("L", 326, 254), ("Z",)], "url(#accent)")
        c.line([(204, 286), (294, 286)], WHITE, 8)
        c.line([(204, 316), (294, 316)], WHITE, 8)
        c.line([(204, 346), (261, 346)], CYAN, 8)
    elif kind == "calendar":
        c.rounded((152, 224, 360, 375), 23, "#173651", WHITE, 8)
        c.line([(157, 269), (355, 269)], WHITE, 7)
        c.line([(200, 207), (200, 241)], CYAN, 12)
        c.line([(312, 207), (312, 241)], CYAN, 12)
        for x, y in ((185, 291), (236, 291), (287, 291), (185, 333), (236, 333)):
            c.rounded((x, y, x+24, y+20), 5, WHITE)
        c.line([(290, 341), (299, 350), (319, 328)], CYAN, 9)
    elif kind == "automation":
        c.line([(185, 255), (256, 255), (256, 336), (328, 336)], CYAN, 9)
        c.line([(256, 255), (328, 255)], CYAN, 9)
        c.rounded((143, 220, 213, 290), 19, "#173651", WHITE, 8)
        c.rounded((301, 220, 371, 290), 19, "#173651", WHITE, 8)
        c.rounded((301, 309, 371, 379), 19, "#173651", WHITE, 8)
        c.path([("M", 179, 236), ("L", 169, 255), ("L", 180, 255), ("L", 173, 275), ("L", 190, 251), ("L", 180, 251), ("Z",)], CYAN)
        c.line([(325, 248), (331, 254), (345, 240)], CYAN, 6)
        c.line([(325, 341), (331, 347), (345, 333)], CYAN, 6)
    elif kind == "research":
        c.line([(157, 240), (206, 240)], MUTED, 7)
        c.line([(157, 273), (190, 273)], MUTED, 7)
        c.line([(157, 306), (198, 306)], MUTED, 7)
        c.line([(305, 324), (353, 372)], CYAN, 22)
        c.ellipse((210, 219, 329, 338), "#173651", WHITE, 10)
        c.line([(244, 284), (260, 269), (272, 280), (294, 255)], CYAN, 8)


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

    sheet = Image.new("RGB", (1200, 656), "#eef4f8")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 20)
        heading_font = ImageFont.truetype("DejaVuSans.ttf", 25)
    except OSError:
        font = heading_font = ImageFont.load_default()
    draw.text((36, 22), "BLUEPRINT / FOLDER ICONS", fill=NAVY, font=heading_font)
    for i, (kind, preview) in enumerate(zip(KINDS, previews)):
        x = 18 + (i % 4) * 296
        y = 66 + (i // 4) * 288
        draw.rounded_rectangle((x, y, x+280, y+272), 22, fill="white")
        sheet.paste(preview.resize((232, 232), Image.Resampling.LANCZOS), (x+24, y-3), preview.resize((232, 232), Image.Resampling.LANCZOS))
        draw.text((x+140, y+233), kind.upper(), fill=NAVY, font=font, anchor="mm")
    sheet.save(output / "contact-sheet.png", optimize=True)
    print(f"Built {len(KINDS)} icon families and contact-sheet.png in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "assets" / "icons")
    args = parser.parse_args()
    build(args.output)
