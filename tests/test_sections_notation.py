import shutil
import subprocess

import pytest
from PIL import Image

from jammit_to_mpak.notation import Geometry, build_pdf
from jammit_to_mpak.sections import convert_sections


def test_section_names_numbers_and_types():
    secs, warns = convert_sections(
        [{"beat": 0, "number": 0, "type": 0}, {"beat": 4, "number": 1, "type": 1}, {"beat": 8, "number": 0, "type": 25},
         {"beat": 12, "number": 2, "type": 4}, {"beat": 15, "number": 0, "type": 9}], [i * 500 for i in range(16)])
    assert secs == [{"name": "Intro 1", "type": "intro", "timeMs": 2000}, {"name": "Guitar solo", "type": "solo", "timeMs": 4000},
                    {"name": "Chorus 2", "type": "chorus", "timeMs": 6000}]
    assert warns == []


def test_unknown_code_is_named_and_warned():
    secs, warns = convert_sections([{"beat": 1, "number": 1, "type": 99}], [0, 500])
    assert secs == [{"name": "Section 99 1", "timeMs": 500}]
    assert "99" in warns[0]


def test_section_on_a_missing_beat_is_dropped():
    secs, warns = convert_sections([{"beat": 50, "number": 1, "type": 1}], [0, 500])
    assert secs == [] and warns


def test_place_is_a_fraction_of_the_page():
    g = Geometry(width=200, interval=40, per_page=4, systems=8)
    assert g.pages == 2 and g.page_height == 160
    assert g.place(0, 100) == (1, 0.5, 20 / 160)
    assert g.place(5, 20) == (2, 0.1, (1 * 40 + 20) / 160)
    assert g.place(3, 999)[1] == 1.0  # beyond the page is held at the edge


def test_pages_hold_the_systems_in_order(tmp_path):
    strips = []
    for i in range(2):  # two strips of four systems, each system a different gray level
        img = Image.new("RGBA", (200, 160), (255, 255, 255, 255))
        for s in range(4):
            level = 20 + 25 * (i * 4 + s)
            img.paste((level, level, level, 255), (0, s * 40, 200, s * 40 + 40))
        p = tmp_path / f"s{i}"
        img.save(p, "PNG")
        strips.append(p)
    pdf = tmp_path / "out.pdf"
    geo = build_pdf(strips, pdf, interval=40, systems=7, per_page=3, quality=95)
    assert (geo.pages, geo.page_height) == (3, 120)
    if not shutil.which("pdftoppm"):
        pytest.skip("pdftoppm not installed")
    subprocess.run(["pdftoppm", "-r", "100", "-png", str(pdf), str(tmp_path / "pg")], check=True)
    page2 = Image.open(tmp_path / "pg-2.png").convert("L")
    assert page2.size == (200, 120)
    for row in range(3):  # page 2 holds systems 3, 4, 5
        want = 20 + 25 * (3 + row)
        assert abs(page2.getpixel((100, row * 40 + 20)) - want) <= 6
    page3 = Image.open(tmp_path / "pg-3.png").convert("L")
    assert page3.getpixel((100, 20)) < 200          # system 6 is there
    assert page3.getpixel((100, 100)) > 245         # the rest of the page is white
