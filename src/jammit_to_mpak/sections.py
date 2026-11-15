"""Names for Jammit's section codes (`sections.plist`).

The codes were read off the section titles printed in the notation of real songs. Codes 0 and 9 are not parts of the
music: they mark the start and the end of the beat grid, so they are left out.
"""
from __future__ import annotations

# code -> (name, Music Pack section type)
SECTION_TYPES: dict[int, tuple[str, str]] = {
    1: ("Intro", "intro"),
    2: ("Verse", "verse"),
    3: ("Pre-chorus", "pre-chorus"),
    4: ("Chorus", "chorus"),
    7: ("Outro", "outro"),
    11: ("Instrumental", "instrumental"),
    13: ("B-section", "bridge"),
    17: ("Interlude", "interlude"),
    19: ("Pre-verse", "pre-verse"),
    23: ("C-section", "bridge"),
    25: ("Guitar solo", "solo"),
    26: ("Keyboard solo", "solo"),
    36: ("Transition", "transition"),
}
BOUNDARY_CODES = {0, 9}


def _int(value, default=None):
    """`value` as an int when it is one or a numeric string (plists may store either), else `default`."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def convert_sections(sections: list[dict], beat_times_ms: list[int]) -> tuple[list[dict], list[str]]:
    """The Music Pack `sections` of a beat file, and warnings for what could not be placed."""
    out: list[dict] = []
    warnings: list[str] = []
    # Beats and codes may arrive as strings; unreadable beats sort first and are reported below.
    for s in sorted(sections, key=lambda s: _int(s.get("beat"), -1)):
        code, beat, number = _int(s.get("type"), s.get("type")), _int(s.get("beat")), _int(s.get("number"), 0)
        if code in BOUNDARY_CODES:
            continue
        if beat is None or not 0 <= beat < len(beat_times_ms):
            warnings.append(f"section type {code} names beat {beat}, which does not exist")
            continue
        name, kind = SECTION_TYPES.get(code, (f"Section {code}", None))
        if code not in SECTION_TYPES:
            warnings.append(f"unknown section code {code}, written as '{name}'")
        entry = {"name": f"{name} {number}" if number else name, "timeMs": beat_times_ms[beat]}
        if kind:
            entry["type"] = kind
        if out and entry["timeMs"] <= out[-1]["timeMs"]:
            warnings.append(f"section '{entry['name']}' starts at the same time as the one before, left out")
            continue
        out.append(entry)
    return out, warnings
