"""Builds a PDF from Jammit's notation strips, and maps a beat's place in the strips to a place in that PDF.

The strips of one track are PNG slices of one tall image (`<id>_jcfn_00`, `_01`, ...). That image is a stack of systems
(rows of music), each `scoreSystemInterval` pixels tall. The PDF puts `per_page` systems on every page, so a place in the
strips (system, x) becomes a page and a position as fractions of the page, which is what a Music Pack `pdf` sync point
needs (SPEC section 9.2). The pages are built here and have no margins, so the arithmetic is exact.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

DEFAULT_INTERVAL = 129  # pixels between system tops, when tracks.plist does not say


@dataclass(frozen=True)
class Geometry:
    width: int       # page width in pixels (the strips' width)
    interval: int    # height of one system in pixels
    per_page: int
    systems: int     # systems that are used (the rest of the strips is blank and left out)

    @property
    def page_height(self) -> int:
        return self.interval * self.per_page

    @property
    def pages(self) -> int:
        return max(1, -(-self.systems // self.per_page))

    def place(self, system: int, x_px: float) -> tuple[int, float, float]:
        """(1-based page, x, y) as fractions of the page, y at the middle of the system."""
        page = system // self.per_page + 1
        row = system % self.per_page
        x = min(1.0, max(0.0, x_px / self.width))
        y = (row * self.interval + self.interval / 2) / self.page_height
        return page, x, y


def stack_strips(strips: list[Path]) -> Image.Image:
    """The strips, top to bottom, as one grayscale image on white."""
    tiles = [Image.open(p).convert("RGBA") for p in strips]
    width = max(t.width for t in tiles)
    height = sum(t.height for t in tiles)
    out = Image.new("RGB", (width, height), (255, 255, 255))
    y = 0
    for t in tiles:
        out.paste(t, (0, y), t)
        y += t.height
    return out.convert("L")


def build_pdf(strips: list[Path], out: Path, interval: int, systems: int | None, per_page: int, quality: int) -> Geometry:
    """Writes the PDF and returns the geometry of its pages. `systems` is how many systems to keep (None: all)."""
    tall = stack_strips(strips)
    available = tall.height // interval
    systems = available if systems is None else min(systems, available)
    if systems < 1:
        raise ValueError("the notation strips hold no system")
    geo = Geometry(width=tall.width, interval=interval, per_page=per_page, systems=systems)
    pages = []
    for p in range(geo.pages):
        page = Image.new("L", (geo.width, geo.page_height), 255)
        for row in range(per_page):
            s = p * per_page + row
            if s >= systems:
                break
            page.paste(tall.crop((0, s * interval, geo.width, (s + 1) * interval)), (0, row * interval))
        pages.append(page)
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=100.0, quality=quality)
    return geo
