"""Lossless, in-memory export of the complete case-library KV value."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile


def build_case_export(payload: Any) -> bytes:
    """Package a detached snapshot without migrating, filtering or trimming it."""
    data = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    guide = (Path(__file__).resolve().parent / "docs" / "CASE_EXPORT.md").read_bytes()
    with BytesIO() as buffer:
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("liuyao_case_library.json", data)
            archive.writestr("README.md", guide)
        return buffer.getvalue()
