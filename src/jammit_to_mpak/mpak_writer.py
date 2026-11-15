"""Writes the ZIP container of a .mpak (SPEC section 3): manifest first, audio Stored, the rest Deflated, ZIP64 when needed."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

_DATE = (1980, 1, 1, 0, 0, 0)  # fixed, so the same input gives the same file
_CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


class MpakWriter:
    def __init__(self, path: Path):
        self._zf = zipfile.ZipFile(path, "w", allowZip64=True)
        self._names: set[str] = set()

    def _info(self, name: str, store: bool, size: int) -> zipfile.ZipInfo:
        key = name.lower()
        if key in self._names:
            raise ValueError(f"duplicate archive entry: {name}")
        self._names.add(key)
        info = zipfile.ZipInfo(name, _DATE)
        info.compress_type = zipfile.ZIP_STORED if store else zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        info.file_size = size
        return info

    def add_bytes(self, name: str, data: bytes, store: bool = False) -> None:
        self._zf.writestr(self._info(name, store, len(data)), data)

    def add_json(self, name: str, doc: dict, indent: int | None = None) -> None:
        text = json.dumps(doc, indent=indent, ensure_ascii=False, separators=(",", ": ") if indent else (",", ":"))
        self.add_bytes(name, text.encode("utf-8"))

    def add_file(self, name: str, path: Path, store: bool) -> None:
        size = path.stat().st_size
        info = self._info(name, store, size)
        with open(path, "rb") as src, self._zf.open(info, "w", force_zip64=size >= (1 << 31)) as dst:
            while chunk := src.read(_CHUNK):
                dst.write(chunk)

    def close(self) -> None:
        self._zf.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
