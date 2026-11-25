"""In-memory ZIP export of stored Liuyao cases."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

if __package__:
    from .case_library import feedback_closed
else:
    from case_library import feedback_closed


def build_case_export(payload: Any, *, feedback_only: bool = False) -> bytes:
    """Package a detached snapshot, preserving stored records and library metadata."""
    if feedback_only:
        cases = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(cases, list):
            cases = []
        selected = [case for case in cases if isinstance(case, dict) and feedback_closed(case)]
        payload = {**payload, "cases": selected} if isinstance(payload, dict) else selected

    data = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    guide = (Path(__file__).resolve().parent / "docs" / "CASE_EXPORT.md").read_bytes()
    with BytesIO() as buffer:
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("liuyao_case_library.json", data)
            archive.writestr("README.md", guide)
        return buffer.getvalue()
