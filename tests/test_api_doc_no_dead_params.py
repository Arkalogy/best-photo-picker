"""Guard against ``docs/API.md`` documenting endpoint parameters the
live handler no longer reads.

Why this exists
---------------
Today's session removed the ``force`` parameter from ``/api/v1/export``
(the prior behavior silently ``rmtree``d the destination, which destroyed
user data when pointed at shared folders like ``~/Downloads``). The
parameter table in ``docs/API.md`` still listed ``force``. Integrators
trust ``docs/API.md`` more than the README; a documented-but-dead
parameter is a contract drift that's worse than a silent removal —
their automation appears to work, then silently produces wrong results.

Test contract
-------------
For the ``/api/v1/export`` parameter table, every backtick-quoted
param name must appear as ``params.get("<name>")`` (or
``request.get_json`` extraction) somewhere in ``bpp/web/bp_export.py``.
If the doc says it, the code must read it.

Scope is intentionally narrow (export only) — extending the scan to
all endpoints is good follow-up work but out of scope for the fix
that closes the immediate drift.
"""

from __future__ import annotations

import pathlib
import re

EXPORT_TABLE_HEADER = "### POST /api/v1/export"
TABLE_ROW_RE = re.compile(r"^\|\s*`([a-z_]+)`\s*\|")


def _params_documented_in_api_md() -> set[str]:
    md = pathlib.Path("docs/API.md").read_text().splitlines()
    in_section = False
    params: set[str] = set()
    for line in md:
        if line.startswith(EXPORT_TABLE_HEADER):
            in_section = True
            continue
        if not in_section:
            continue
        # Any subsequent ### header ends the section. The export
        # header itself was consumed by the branch above so the next
        # ### we see is always the boundary to the next endpoint.
        if line.startswith("### "):
            break
        m = TABLE_ROW_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        # Skip the table header row ("| Param | Type | ...").
        if name in {"param"}:
            continue
        params.add(name)
    return params


def _params_read_by_handler() -> set[str]:
    src = pathlib.Path("bpp/web/bp_export.py").read_text()
    # Match every params.get("name") + every params["name"] in the file.
    reads: set[str] = set()
    for m in re.finditer(r'params\.get\(\s*"([a-z_]+)"', src):
        reads.add(m.group(1))
    for m in re.finditer(r'params\[\s*"([a-z_]+)"\s*\]', src):
        reads.add(m.group(1))
    return reads


def test_export_endpoint_doc_matches_handler() -> None:
    """Every parameter documented in the /api/v1/export table must be
    read by the export handler. A documented-but-unused parameter is
    contract drift (today's ``force`` removal regression).
    """
    documented = _params_documented_in_api_md()
    assert documented, (
        "Could not parse any parameters from the /api/v1/export "
        "section of docs/API.md — did the section header change?"
    )
    read = _params_read_by_handler()

    # ``outdir`` and ``selected_paths`` are extracted via dedicated
    # validation helpers and explicit guards, not via the generic
    # ``params.get`` pattern. Both are required, both verified by
    # other tests, allowlist them so the scan stays narrow.
    documented_dynamic = documented - {"outdir", "selected_paths"}

    dead = sorted(documented_dynamic - read)
    assert not dead, (
        f"docs/API.md documents parameter(s) {dead!r} for "
        f"/api/v1/export but bpp/web/bp_export.py never reads them. "
        f"Either restore the feature or remove the row from the docs."
    )
