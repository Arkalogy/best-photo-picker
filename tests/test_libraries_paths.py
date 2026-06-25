"""R8-M10: WebAppState path extraction.

Lock the new contract:
  - `ctx.paths` is a `LibraryPaths` dataclass holding library_path,
    workdir, input_dir, dirs.
  - `ctx.library_path` / `ctx.workdir` / `ctx.input_dir` are
    property accessors (read + write) that delegate to `ctx.paths`
    AND keep the legacy `ctx.state["..."]` dict in sync.
  - `switch_library` updates both forms atomically.

This is half of the audit's M10 ask. Subsequent sessions can
extract WorkerManager / CacheManager / HealthChecker on the
same property-shim pattern; this commit demonstrates the
template and migrates the 15 path-read callsites across six
blueprints.
"""

from __future__ import annotations

import os

import pytest


def test_paths_dataclass_populated_from_constructor(tmp_path):
    from bpp.web.state import LibraryPaths, WebAppState

    workdir = str(tmp_path / "wd")
    lib = str(tmp_path / "lib")
    os.makedirs(workdir)

    ws = WebAppState(workdir=workdir, library_path=lib)
    try:
        assert isinstance(ws.paths, LibraryPaths)
        assert ws.paths.library_path == os.path.abspath(lib)
        assert ws.paths.workdir == os.path.abspath(workdir)
        assert ws.paths.dirs  # populated by get_library_dirs
    finally:
        ws.shutdown()


def test_property_accessors_delegate_to_paths(tmp_path):
    from bpp.web.state import WebAppState

    workdir = str(tmp_path / "wd")
    lib = str(tmp_path / "lib")
    os.makedirs(workdir)

    ws = WebAppState(workdir=workdir, library_path=lib)
    try:
        assert ws.library_path == ws.paths.library_path
        assert ws.workdir == ws.paths.workdir
        assert ws.input_dir == ws.paths.input_dir
    finally:
        ws.shutdown()


def test_setter_updates_both_forms(tmp_path):
    """A write through the property setter must update BOTH
    `ctx.paths.X` AND `ctx.state["X"]` so a caller reading either
    form mid-mutation sees consistent values."""
    from bpp.web.state import WebAppState

    workdir = str(tmp_path / "wd")
    lib = str(tmp_path / "lib")
    os.makedirs(workdir)

    ws = WebAppState(workdir=workdir, library_path=lib)
    try:
        new_input = str(tmp_path / "new_input")
        ws.input_dir = new_input
        assert ws.paths.input_dir == new_input
        assert ws.state["input_dir"] == new_input

        new_wd = str(tmp_path / "new_wd")
        ws.workdir = new_wd
        assert ws.paths.workdir == new_wd
        assert ws.state["workdir"] == new_wd
    finally:
        ws.shutdown()


def test_switch_library_updates_paths_and_state(tmp_path):
    from bpp.web.state import WebAppState

    workdir = str(tmp_path / "wd")
    lib = str(tmp_path / "lib")
    os.makedirs(workdir)

    ws = WebAppState(workdir=workdir, library_path=lib)
    try:
        new_lib = str(tmp_path / "new_lib")
        os.makedirs(new_lib)
        ws.switch_library(new_lib)

        # Both reads return the new library
        assert ws.paths.library_path == new_lib
        assert ws.state["library_path"] == new_lib
        assert ws.library_path == new_lib

        # And the workdir migrated to the new lib's data subdir
        assert ws.paths.workdir == ws.state["workdir"]
        assert ws.workdir == ws.paths.workdir
    finally:
        ws.shutdown()


def test_paths_dirs_are_immutable(tmp_path):
    """R10-M2: ``frozen=True`` only blocks REASSIGNING fields. The
    pre-fix ``dirs: dict[str, str]`` could still be mutated per-key
    (``ctx.paths.dirs["thumbs"] = "/elsewhere"``), bypassing the
    invariant the freeze was meant to enforce. Wrap dirs in
    ``MappingProxyType`` so per-key assignment raises TypeError."""
    from bpp.web.state import WebAppState

    workdir = str(tmp_path / "wd")
    lib = str(tmp_path / "lib")
    os.makedirs(workdir)
    ws = WebAppState(workdir=workdir, library_path=lib)
    try:
        # Per-key write through ctx.paths.dirs must raise.
        with pytest.raises(TypeError):
            ws.paths.dirs["data"] = "/tmp/evil"

        # And the legacy ctx.dirs alias must point at the SAME
        # immutable mapping — pre-fix it was a separate raw dict
        # that callers could mutate even when paths.dirs was frozen.
        with pytest.raises(TypeError):
            ws.dirs["data"] = "/tmp/evil"

        # Read-only access keeps working.
        assert "data" in ws.paths.dirs
        assert ws.dirs["data"] == ws.paths.dirs["data"]
    finally:
        ws.shutdown()


def test_switch_library_replaces_dirs_immutably(tmp_path):
    """After a switch, both ctx.paths.dirs and ctx.dirs must be
    immutable views over the NEW library's directory map. Pre-fix,
    `ctx.dirs = new_dirs` aliased the raw dict from
    `get_library_dirs(...)`, so the immutability gain at __init__
    didn't survive a library switch."""
    from bpp.web.state import WebAppState

    workdir = str(tmp_path / "wd")
    lib = str(tmp_path / "lib")
    os.makedirs(workdir)
    ws = WebAppState(workdir=workdir, library_path=lib)
    try:
        new_lib = str(tmp_path / "new_lib")
        os.makedirs(new_lib)
        ws.switch_library(new_lib)

        with pytest.raises(TypeError):
            ws.paths.dirs["data"] = "/tmp/evil"
        with pytest.raises(TypeError):
            ws.dirs["photos"] = "/tmp/evil"
    finally:
        ws.shutdown()


def test_library_paths_is_frozen(tmp_path):
    """R9-reliability-M3: ``LibraryPaths`` must be frozen so a caller
    that does ``ctx.paths.library_path = X`` directly hits a
    FrozenInstanceError instead of silently desyncing
    ``ctx.state["library_path"]``. The only sanctioned writes go
    through the property setters, which use ``dataclasses.replace``
    under the hood and update both forms together."""
    import dataclasses

    from bpp.web.state import LibraryPaths, WebAppState

    workdir = str(tmp_path / "wd")
    lib = str(tmp_path / "lib")
    os.makedirs(workdir)
    ws = WebAppState(workdir=workdir, library_path=lib)
    try:
        # 1. The dataclass itself is frozen.
        assert LibraryPaths.__dataclass_params__.frozen is True

        # 2. Direct attribute assignment raises.
        with pytest.raises(dataclasses.FrozenInstanceError):
            ws.paths.library_path = "/elsewhere"

        # 3. The property setter (the only sanctioned path) still
        #    works and keeps both forms in sync.
        ws.library_path = str(tmp_path / "via_setter")
        assert ws.paths.library_path == str(tmp_path / "via_setter")
        assert ws.state["library_path"] == str(tmp_path / "via_setter")
    finally:
        ws.shutdown()
