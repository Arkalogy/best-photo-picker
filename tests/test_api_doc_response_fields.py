"""``docs/API.md`` names every field two licensing endpoints return.

Integrators read ``docs/API.md`` rather than the handlers. The pending
dialog's ``blocked[]`` items and the acceptance list's freshness flags
(``current`` / ``needs_reaccept``) and ``withdrawn_by_damage`` were added
to the code without the doc following; this keeps the two together: the
item's keys come from the code that builds it, so a new key fails the
test until the doc names it.
"""

from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

from bpp.registry import LicenseClass
from bpp.web.model_filter import _blocked_item

API_MD = pathlib.Path(__file__).resolve().parents[1] / "docs" / "API.md"


def _section(header: str) -> str:
    """The doc text from the line that is exactly ``header`` up to the
    next heading of any level (a TOC entry or a longer sibling header
    doesn't match; a ``###`` inside a code block doesn't end it)."""
    text = API_MD.read_text(encoding="utf-8")
    start = re.search(rf"^{re.escape(header)}$", text, re.MULTILINE)
    assert start, f"no heading line {header!r} in docs/API.md"
    rest = text[start.end() :]
    out, fenced = [], False
    for line in rest.splitlines(keepends=True):
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"^#{1,6} ", line):
            break
        out.append(line)
    return "".join(out)


def _blocked_example(section: str) -> str:
    """Just the ``"blocked": [ ... ]`` part of the section's JSON example."""
    marker = '"blocked": ['
    start = section.find(marker)
    assert start != -1, f"the example has no {marker} (reflowed?)"
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(section)):
        ch = section[i]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            depth += {"[": 1, "]": -1}.get(ch, 0)
            if ch == "]" and depth == 0:
                return section[start:i]
    raise AssertionError("unterminated blocked[] example")


class TestModelsPending:
    section = "### GET /api/v1/models/pending"

    def test_every_blocked_item_key_is_documented(self) -> None:
        legal = SimpleNamespace(
            id="x",
            display_name="X",
            kind="k",
            default_for_kind=False,
            upstream_claimed_license_class=LicenseClass.MIT,
        )
        keys = _blocked_item("X", 1.0, "host", legal, "needs_license").keys()
        doc = _section(self.section)
        example = _blocked_example(doc)
        missing = [k for k in keys if f'"{k}"' not in example]
        assert not missing, f"blocked[] keys not in the example: {missing}"
        unexplained = [k for k in keys if f"`{k}`" not in doc]
        assert not unexplained, f"blocked[] keys the prose doesn't explain: {unexplained}"

    def test_every_reason_is_documented(self) -> None:
        doc = _section(self.section)
        for reason in ("needs_license", "needs_reaccept", "status_blocked"):
            assert f"`{reason}`" in doc, reason


class TestAcceptanceList:
    section = "### GET /api/v1/model-registry/acceptance/list"

    def test_row_freshness_and_event_are_documented(self) -> None:
        doc = _section(self.section)
        for field in ("event", "current", "needs_reaccept"):
            assert f'"{field}"' in doc, field

    def test_withdrawn_by_damage_is_documented(self) -> None:
        assert '"withdrawn_by_damage"' in _section(self.section)
