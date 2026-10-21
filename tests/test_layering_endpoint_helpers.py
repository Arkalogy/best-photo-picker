"""Shared helpers live outside the endpoint files.

The DB layer imported a sibling-attaching helper from an albums endpoint
file, and several web modules imported cache, media, catalog and CLIP-cap
helpers from the endpoint file that happened to define them. Endpoint
files (``bp_*.py``) hold routes; what more than one of them needs lives in
a plain module.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "bpp"

#: Edges kept for now; each names the work that removes it.
ALLOWED = {
    # The install claim / lock machinery is pinned by its endpoint tests;
    # moving it is the install-guard split in the backlog.
    ("web/install_status.py", "bp_install"),
    ("web/bp_clip.py", "bp_install"),
    ("web/bp_faces_extract.py", "bp_install"),
    ("web/bp_library.py", "bp_install"),
    ("web/bp_inpaint.py", "bp_install"),
    ("web/install_stream.py", "bp_install"),
    ("web/state_lifecycle.py", "bp_photos_lifecycle"),
    # The faces blueprints share one Blueprint object.
    ("web/bp_faces_recluster.py", "bp_faces_cluster_ops"),
    # Route registration for the shared Blueprint (module import form).
    ("web/bp_faces_cluster_ops.py", "bp_faces_recluster"),
    # Photo lifecycle routes are registered on the photos-manage blueprint.
    ("web/bp_photos_manage.py", "bp_photos_lifecycle"),
}

_IMPORT = re.compile(r"^\s*from bpp\.web\.(bp_[a-z_]+) import", re.MULTILINE)
# ``from bpp.web import bp_x [as _b]`` and ``import bpp.web.bp_x``: the
# same edge in another spelling.
_MODULE_IMPORT = re.compile(
    r"^\s*(?:from bpp\.web import (bp_[a-z_]+)|import bpp\.web\.(bp_[a-z_]+))", re.MULTILINE
)


def _edges():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in ("web/app.py", "web/blueprints.py") or rel.startswith("plugins/"):
            continue
        text = path.read_text(encoding="utf-8")
        for m in _IMPORT.finditer(text):
            yield rel, m.group(1)
        for m in _MODULE_IMPORT.finditer(text):
            yield rel, m.group(1) or m.group(2)


def test_the_db_layer_imports_no_endpoint_file():
    offenders = [(f, t) for f, t in _edges() if f.startswith("db/")]
    assert offenders == [], offenders


def test_only_the_known_endpoint_edges_remain():
    offenders = sorted({(f, t) for f, t in _edges() if (f, t) not in ALLOWED})
    assert offenders == [], f"helpers imported from endpoint files: {offenders}"
