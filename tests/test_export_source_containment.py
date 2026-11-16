"""Export reads only from folders the owner chose for the run.

The rows of a library database (or a workdir's analysis.json) decide which
files an export copies, and a library prepared by someone else can name
any file the owner's account can read. The export now takes the folders
it may read from its caller (the library, the workdir, the folder the
owner analyzed or named) and refuses everything else, per file, saying so.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from bpp.output.export import export_selected

REPO_ROOT = Path(__file__).resolve().parent.parent


def _jpeg(path) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (8, 8), "red").save(path, "JPEG")
    return str(path)


def _item(path, **extra):
    return {"filepath": str(path), "aggregate_score": 0.9, **extra}


def _selected(outdir) -> list[str]:
    folder = os.path.join(outdir, "selected")
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


@pytest.mark.parametrize("mode", ["copy", "hardlink", "symlink"])
def test_a_file_outside_the_source_folders_is_refused(tmp_path, mode):
    inside = _jpeg(tmp_path / "lib" / "photos" / "in.jpg")
    outside = _jpeg(tmp_path / "elsewhere" / "private.jpg")
    out = str(tmp_path / "out")
    items = [_item(outside), _item(inside)]

    result = export_selected(
        items, items, out, mode=mode, gallery=False, source_roots=[str(tmp_path / "lib")]
    )

    assert (result.exported, result.failed, result.outside_source) == (1, 1, 1)
    assert _selected(out) == ["002_in.jpg"]
    failure = result.failures[0]
    assert failure["source"] == outside
    assert "outside" in failure["error"]


def test_a_link_inside_the_library_that_points_out_is_refused(tmp_path):
    outside = _jpeg(tmp_path / "elsewhere" / "private.jpg")
    link = tmp_path / "lib" / "photos" / "looks-local.jpg"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    out = str(tmp_path / "out")
    items = [_item(link)]

    result = export_selected(items, items, out, gallery=False, source_roots=[str(tmp_path / "lib")])

    assert (result.exported, result.outside_source) == (0, 1)
    assert _selected(out) == []


def test_the_gallery_makes_no_thumbnail_of_a_refused_file(tmp_path):
    inside = _jpeg(tmp_path / "lib" / "in.jpg")
    outside = _jpeg(tmp_path / "elsewhere" / "private.jpg")
    out = str(tmp_path / "out")
    items = [_item(outside), _item(inside)]

    export_selected(items, items, out, gallery=True, source_roots=[str(tmp_path / "lib")])

    thumbs = os.listdir(os.path.join(out, "thumbnails"))
    assert [t for t in thumbs if "private" in t] == []
    assert len(thumbs) == 1


def test_a_stored_video_flag_does_not_turn_a_file_into_a_raw_copy(tmp_path):
    note = tmp_path / "lib" / "notes.txt"
    note.parent.mkdir(parents=True)
    note.write_text("not a video")
    out = str(tmp_path / "out")
    items = [_item(note, is_video=1)]

    result = export_selected(items, items, out, gallery=False, source_roots=[str(tmp_path / "lib")])

    assert (result.exported, result.failed) == (0, 1)
    assert _selected(out) == []


# ── bpp pick ─────────────────────────────────────────────────────────────


def _library_with_rows(tmp_path, paths) -> str:
    from bpp.db.connection import close_all_connections, get_db, init_db
    from bpp.db.library import ensure_library_dirs
    from bpp.db.photos import upsert_photo

    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    dirs = ensure_library_dirs(str(lib))
    db_path = os.path.join(dirs["data"], "photopicker.db")
    init_db(db_path)
    conn = get_db(db_path)
    for n, path in enumerate(paths):
        upsert_photo(
            conn,
            {
                "filepath": path,
                "date": "2024-01-01T00:00:00",
                "aggregate_score": 0.9 - n * 0.1,
                "blur_score": 0.5,
                "exposure_score": 0.5,
                "face_score": 0.5,
                "composition_score": 0.5,
            },
        )
    conn.commit()
    close_all_connections()
    return str(lib)


def _pick_ns(library, out, **overrides):
    ns = argparse.Namespace(
        library=library,
        top=10,
        out=out,
        boost_face=[],
        json=False,
        paths_only=True,
        quality="original",
        dry_run=False,
        source_root=[],
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def test_pick_does_not_export_a_file_the_library_names_outside_itself(tmp_path, capsys):
    from bpp import commands

    outside = _jpeg(tmp_path / "elsewhere" / "private.jpg")
    inside = _jpeg(tmp_path / "lib" / "photos" / "in.jpg")
    lib = _library_with_rows(tmp_path, [outside, inside])
    out = str(tmp_path / "out")

    rc = commands.do_pick(_pick_ns(lib, out))

    assert rc == 1, "a run that refused files must not report success"
    assert [n for n in _selected(out) if "private" in n] == []
    assert [n for n in _selected(out) if n.endswith("_in.jpg")] != []
    err = capsys.readouterr().err
    assert "--source-root" in err


def test_pick_exports_from_a_folder_the_owner_names(tmp_path):
    from bpp import commands

    outside = _jpeg(tmp_path / "elsewhere" / "trip.jpg")
    lib = _library_with_rows(tmp_path, [outside])
    out = str(tmp_path / "out")

    rc = commands.do_pick(_pick_ns(lib, out, source_root=[str(tmp_path / "elsewhere")]))

    assert rc == 0
    assert _selected(out) == ["001_trip.jpg"]


# ── bpp select ───────────────────────────────────────────────────────────


def _select_ns(workdir, out, **overrides):
    ns = argparse.Namespace(
        workdir=workdir,
        out=out,
        config=None,
        k=5,
        export_mode="copy",
        gallery=False,
        dry_run=False,
        source_root=[],
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def _workdir_naming(tmp_path, path) -> str:
    workdir = tmp_path / "work"
    workdir.mkdir()
    analysis = [
        {
            "filepath": path,
            "aggregate_score": 1.0,
            "date": "2024-01-01",
            "date_day": "2024-01-01",
            "date_month": "2024-01",
        }
    ]
    (workdir / "analysis.json").write_text(json.dumps(analysis))
    return str(workdir)


@pytest.mark.parametrize("mode", ["copy", "hardlink", "symlink"])
def test_select_does_not_export_a_file_the_workdir_names_outside_itself(tmp_path, mode):
    from bpp import commands

    outside = _jpeg(tmp_path / "elsewhere" / "private.jpg")
    out = str(tmp_path / "out")

    rc = commands.do_select(_select_ns(_workdir_naming(tmp_path, outside), out, export_mode=mode))

    assert rc == 1
    assert _selected(out) == []


def test_select_exports_from_a_folder_the_owner_names(tmp_path):
    from bpp import commands

    outside = _jpeg(tmp_path / "elsewhere" / "trip.jpg")
    out = str(tmp_path / "out")
    ns = _select_ns(
        _workdir_naming(tmp_path, outside), out, source_root=[str(tmp_path / "elsewhere")]
    )

    assert commands.do_select(ns) == 0
    assert _selected(out) == ["001_trip.jpg"]


def test_the_cli_accepts_source_root_on_pick_and_select():
    from bpp.cli import build_parser

    parser = build_parser()
    pick = parser.parse_args(["pick", "/lib", "--source-root", "/a", "--source-root", "/b"])
    assert pick.source_root == ["/a", "/b"]
    select = parser.parse_args(["select", "--workdir", "/w", "--out", "/o", "--source-root", "/a"])
    assert select.source_root == ["/a"]


# ── web export ───────────────────────────────────────────────────────────


def _web_export(tmp_path, *, analyzed_folder: str | None):
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app

    lib = str(tmp_path / "lib")
    os.makedirs(os.path.join(lib, "photos"))
    outside = _jpeg(tmp_path / "elsewhere" / "private.jpg")
    app = create_app(library_path=lib)
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    if analyzed_folder is not None:
        ctx.export_source_dirs.add(analyzed_folder)
    with app.app_context():
        conn = ctx.get_conn()
        upsert_photo(conn, {"filepath": outside, "date": "2024-01-01T00:00:00"})
        conn.commit()
        ctx.invalidate_analysis()
    out = os.path.join(lib, "export")
    with app.test_client() as c:
        r = c.post(
            "/api/v1/export",
            json={"outdir": out, "selected_paths": [outside], "mode": "copy", "gallery": False},
        )
    assert r.status_code == 200, r.get_json()
    return r.get_json(), out


def test_web_export_refuses_a_file_outside_the_library(tmp_path):
    body, out = _web_export(tmp_path, analyzed_folder=None)

    assert (body["count"], body["failed"], body["outside_source"]) == (0, 1, 1)
    assert _selected(out) == []


def test_web_export_allows_the_folder_the_owner_analyzed_in_place(tmp_path):
    body, out = _web_export(tmp_path, analyzed_folder=str(tmp_path / "elsewhere"))

    assert (body["count"], body["outside_source"]) == (1, 0)
    assert _selected(out) == ["001_private.jpg"]


def test_the_export_worker_reports_refused_files(tmp_path):
    from bpp.web.export_worker import ExportWorker

    outside = _jpeg(tmp_path / "elsewhere" / "private.jpg")
    worker = ExportWorker()
    events: list[dict] = []
    worker._emit = events.append  # type: ignore[method-assign]
    items = [_item(outside)]
    worker._run(
        items,
        items,
        str(tmp_path / "out"),
        {},
        "copy",
        False,
        "original",
        None,
        85,
        False,
        False,
        "",
        True,
        [str(tmp_path / "lib")],
    )

    done = [e for e in events if e.get("type") == "done"]
    assert done and done[0]["outside_source"] == 1


# ── every caller names its folders ───────────────────────────────────────


def test_every_export_call_in_the_app_passes_its_source_folders():
    import ast

    calls = []
    for path in (REPO_ROOT / "bpp").rglob("*.py"):
        text = path.read_text()
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            func = ast.unparse(node.func)
            if func.split(".")[-1] == "export_selected" or func.endswith("export_worker.start"):
                calls.append(func)
                named = {kw.arg for kw in node.keywords}
                assert "source_roots" in named, (
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} exports unchecked"
                )
    assert len(calls) >= 5, f"expected pick, select, sync, worker and its start: {calls}"


# ── the check itself ─────────────────────────────────────────────────────


def test_a_path_that_cannot_be_resolved_fails_one_photo_not_the_run(tmp_path):
    inside = _jpeg(tmp_path / "lib" / "in.jpg")
    out = str(tmp_path / "out")
    items = [_item(str(tmp_path / "lib" / "bad\x00name.jpg")), _item(inside)]

    result = export_selected(items, items, out, gallery=False, source_roots=[str(tmp_path / "lib")])

    assert (result.exported, result.failed) == (1, 1)
    assert _selected(out) == ["002_in.jpg"]


def test_a_root_spelled_in_another_case_still_matches_where_the_volume_ignores_case(tmp_path):
    inside = _jpeg(tmp_path / "Lib" / "photos" / "in.jpg")
    other_spelling = str(tmp_path / "lib")
    if not os.path.isdir(other_spelling):
        pytest.skip("this volume is case-sensitive")
    out = str(tmp_path / "out")
    items = [_item(inside)]

    result = export_selected(items, items, out, gallery=False, source_roots=[other_spelling])

    assert (result.exported, result.outside_source) == (1, 0)


def test_a_sibling_folder_with_the_root_as_its_prefix_is_outside(tmp_path):
    from bpp.utils.path_validation import FolderSet

    _jpeg(tmp_path / "lib" / "in.jpg")
    near = _jpeg(tmp_path / "lib-other" / "x.jpg")
    roots = FolderSet([str(tmp_path / "lib"), "", str(tmp_path / "not-there")])

    assert roots.holds(str(tmp_path / "lib" / "in.jpg"))
    assert roots.holds(str(tmp_path / "lib" / "gone" / "missing.jpg"))
    assert not roots.holds(near)
    assert not roots.holds(str(tmp_path / "not-there" / "x.jpg"))
    assert not FolderSet([]).holds(near)


# ── which folders the app's export may read ──────────────────────────────


def _app(tmp_path):
    from bpp.web.app import create_app

    lib = str(tmp_path / "lib")
    os.makedirs(os.path.join(lib, "photos"), exist_ok=True)
    app = create_app(library_path=lib)
    app.config["TESTING"] = True
    return app, app.extensions["bpp"]


def test_an_analyze_that_starts_makes_its_folder_exportable(tmp_path, monkeypatch):
    from bpp.web.bp_export import _source_roots

    app, ctx = _app(tmp_path)
    folder = tmp_path / "trip"
    folder.mkdir()
    monkeypatch.setattr(ctx.worker, "start", lambda **kw: True)
    with app.test_client() as c:
        r = c.post("/api/v1/analyze", json={"input_dir": str(folder)})
    assert r.status_code == 202, r.get_json()
    assert str(folder) in _source_roots(ctx)


def test_an_analyze_that_is_refused_does_not(tmp_path, monkeypatch):
    from bpp.web.bp_export import _source_roots

    app, ctx = _app(tmp_path)
    folder = tmp_path / "trip"
    folder.mkdir()
    monkeypatch.setattr(ctx.worker, "start", lambda **kw: False)
    with app.test_client() as c:
        r = c.post("/api/v1/analyze", json={"input_dir": str(folder)})
    assert r.status_code == 409, r.get_json()
    assert str(folder) not in _source_roots(ctx)


def test_switching_library_forgets_the_folders_analyzed_for_the_last_one(tmp_path):
    from bpp.web.bp_export import _source_roots

    app, ctx = _app(tmp_path)
    ctx.export_source_dirs.add(str(tmp_path / "trip"))
    other = tmp_path / "other-lib"
    other.mkdir()
    with app.app_context():
        ctx.switch_library(str(other))

    roots = _source_roots(ctx)
    assert str(tmp_path / "trip") not in roots
    assert not any(
        r.startswith(str(tmp_path / "lib") + os.sep) or r == str(tmp_path / "lib") for r in roots
    )


def test_a_folder_named_at_launch_is_exportable(tmp_path):
    from bpp.web.state import WebAppState

    folder = tmp_path / "trip"
    folder.mkdir()
    ctx = WebAppState(input_dir=str(folder), library_path=str(tmp_path / "lib"))
    assert ctx.export_source_dirs == {str(folder)}
    assert WebAppState(library_path=str(tmp_path / "lib2")).export_source_dirs == set()


# ── command-line folders ─────────────────────────────────────────────────


def test_run_takes_source_root_and_passes_it_with_the_input_folder(tmp_path, monkeypatch):
    from bpp import commands
    from bpp.cli import build_parser

    args = build_parser().parse_args(
        ["run", "--input", str(tmp_path), "--out", str(tmp_path / "o"), "--source-root", "/nas"]
    )
    seen = {}
    monkeypatch.setattr("bpp.commands.analyze.do_analyze", lambda ns: 0)
    monkeypatch.setattr(
        "bpp.commands.analyze.do_select", lambda ns: seen.update(roots=ns.source_root) or 0
    )
    assert commands.do_run(args) == 0
    assert seen["roots"] == [str(tmp_path), "/nas"]


def test_a_source_root_that_does_not_exist_is_an_error_up_front(tmp_path, capsys):
    from bpp import commands

    lib = _library_with_rows(tmp_path, [_jpeg(tmp_path / "lib" / "photos" / "in.jpg")])
    out = str(tmp_path / "out")
    gone = str(tmp_path / "typo")

    assert commands.do_pick(_pick_ns(lib, out, source_root=[gone])) == 1
    assert "does not exist" in capsys.readouterr().err
    assert _selected(out) == []

    workdir = _workdir_naming(tmp_path, _jpeg(tmp_path / "elsewhere" / "a.jpg"))
    assert commands.do_select(_select_ns(workdir, out, source_root=[gone])) == 1
    assert _selected(out) == []


def test_a_source_root_may_start_with_a_tilde(tmp_path, monkeypatch):
    from bpp.utils.path_validation import split_existing_folders

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Photos").mkdir()
    assert split_existing_folders(["~/Photos", "~/nope"]) == (
        [str(tmp_path / "Photos")],
        [str(tmp_path / "nope")],
    )
