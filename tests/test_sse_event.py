"""One writer of the Server-Sent Events wire format."""

from __future__ import annotations

import json
import pathlib
import re

from bpp.web.sse import sse_event

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_a_frame_is_one_data_line_and_a_blank_line():
    frame = sse_event({"type": "progress", "done": 3, "total": 9})
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    assert json.loads(frame[len("data: ") : -2]) == {"type": "progress", "done": 3, "total": 9}


def test_no_module_writes_a_frame_by_hand():
    frame_literal = re.compile(r"""["']data:""")
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "bpp").rglob("*.py")
        if p.name != "sse.py" and frame_literal.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"use sse_event(): {offenders}"
