"""The picker's fallback tables in the JavaScript match the server's.

The server sends ``legacy_label`` / ``toggle_key`` / ``method_value`` with
every registry entry; the JavaScript keeps a copy for an entry that
arrives without them. This keeps the copy identical to the source.
"""

from __future__ import annotations

import pathlib
import re

from bpp.scoring.face_embedder_registry import list_embedders
from bpp.web.picker_fields import _BUILTIN_FEATURES

JS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "bpp/web/static/js/modules/face-embedders-catalog.mjs"
).read_text()


def _js_object(name: str) -> str:
    m = re.search(rf"export const {name} = \{{(.*?)\n\}};", JS, re.S)
    assert m, name
    return m.group(1)


def test_legacy_feature_map_matches_the_server_table():
    body = _js_object("LEGACY_FEATURE_MAP")
    rows = re.findall(
        r"^\s*([a-z0-9_]+): \{ legacy_label: (null|\"[^\"]*\"), toggle_key: (null|\"[^\"]*\") \},",
        body,
        re.M,
    )
    js = {
        rid: (None if lab == "null" else lab.strip('"'), None if tog == "null" else tog.strip('"'))
        for rid, lab, tog in rows
    }
    assert js == _BUILTIN_FEATURES


def test_method_values_match_the_registered_embedders():
    import bpp.scoring.face  # noqa: F401  (registers the built-ins)

    body = _js_object("FACE_EMBEDDER_METHOD_VALUE")
    js = dict(re.findall(r"^\s*([a-z0-9_]+): \"([a-z0-9_]+)\",", body, re.M))
    builtin = {e.registry_id: e.name for e in list_embedders() if e.registry_id in js}
    assert js == builtin
