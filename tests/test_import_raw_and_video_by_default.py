"""The app's Import takes camera RAW photos and videos without any setup.

Before 0.2.2 the app passed its own short list (jpg, jpeg, png, heic) to
the import, so a folder of RAW photos or videos imported nothing, with
"0 found", although the library's own default named them.
"""

from __future__ import annotations

from bpp.config import DEFAULTS, parse_scan_extensions
from bpp.db.library import DEFAULT_EXTENSIONS
from bpp.io_scan import scan_images
from bpp.utils.raw import RAW_EXTENSIONS
from bpp.utils.video import VIDEO_EXTENSIONS


def test_the_default_list_names_every_raw_and_video_format():
    default = set(parse_scan_extensions(DEFAULTS["scan_extensions"]))
    for ext in RAW_EXTENSIONS | VIDEO_EXTENSIONS:
        assert ext.lstrip(".") in default, ext
    assert {"jpg", "jpeg", "png", "heic"} <= default


def test_the_library_and_the_app_share_one_list():
    assert parse_scan_extensions(DEFAULTS["scan_extensions"]) == DEFAULT_EXTENSIONS


def test_the_app_import_finds_raw_photos_and_videos(tmp_path):
    src = tmp_path / "card"
    src.mkdir()
    for name in (
        "DSC_0001.NEF",
        "IMG_0002.CR3",
        "clip.MOV",
        "clip2.mp4",
        "IMG_0003.JPG",
        "notes.txt",
    ):
        (src / name).write_bytes(b"x")

    from bpp.web.state import WebAppState

    ws = WebAppState(workdir=str(tmp_path / "wd"), library_path=str(tmp_path / "lib"))
    try:
        found = {
            p.rsplit("/", 1)[1]
            for p in scan_images(str(src), extensions=ws.state["extensions"], recursive=True)
        }
    finally:
        ws.shutdown()
    assert found == {"DSC_0001.NEF", "IMG_0002.CR3", "clip.MOV", "clip2.mp4", "IMG_0003.JPG"}


def test_scoring_an_imported_raw_keeps_it_marked_raw(tmp_path):
    """The import marks the file RAW; the scored row it gets next carries no
    RAW flag and used to blank it, so the RAW filter never showed it."""
    from bpp.db.connection import init_db
    from bpp.db.photos import bulk_upsert_photos

    conn = init_db(str(tmp_path / "photopicker.db"))
    path = str(tmp_path / "DSC_0001.NEF")
    bulk_upsert_photos(conn, [{"filepath": path, "is_raw": 1, "sha256": "ab"}])
    bulk_upsert_photos(conn, [{"filepath": path, "aggregate_score": 0.7}])
    row = conn.execute(
        "SELECT is_raw, is_video, aggregate_score FROM photos WHERE filepath = ?", (path,)
    ).fetchone()
    assert tuple(row) == (1, 0, 0.7)


def test_rows_from_analyze_are_marked_by_their_file_type(tmp_path):
    """Analyze writes scored rows with no flags at all: the file's type
    decides, and a plain photo is 0/0 (not blank), so the stats count it."""
    from bpp.db.connection import init_db
    from bpp.db.photos import bulk_upsert_photos, upsert_photo
    from bpp.db.stats import get_library_stats

    conn = init_db(str(tmp_path / "photopicker.db"))
    names = {"a.NEF": (0, 1), "b.mov": (1, 0), "c.jpg": (0, 0)}
    bulk_upsert_photos(conn, [{"filepath": str(tmp_path / n)} for n in ("a.NEF", "b.mov")])
    upsert_photo(conn, {"filepath": str(tmp_path / "c.jpg")})
    for name, flags in names.items():
        row = conn.execute(
            "SELECT is_video, is_raw FROM photos WHERE filepath = ?", (str(tmp_path / name),)
        ).fetchone()
        assert tuple(row) == flags, name
    stats = get_library_stats(conn)
    assert (stats["photo_count"], stats["video_count"], stats["raw_count"]) == (1, 1, 1)


def _cli_extensions(tmp_path, monkeypatch, *argv):
    """The extension list ``bpp analyze`` scans with (the scan is stubbed)."""
    import bpp.io_scan
    from bpp.cli import main

    seen = {}

    def fake_scan(_path, extensions, **_kw):
        seen["extensions"] = extensions
        return []

    monkeypatch.setattr(bpp.io_scan, "scan_images", fake_scan)
    (tmp_path / "in").mkdir(exist_ok=True)
    head = [a for a in argv if a.startswith("--extensions")]
    rest = [a for a in argv if not a.startswith("--extensions")]
    cmd = ["analyze", "--input", str(tmp_path / "in"), "--out", str(tmp_path / "wd")]
    main([*head, *cmd, *rest])
    return seen["extensions"]


def test_the_cli_follows_the_config_file_list(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scan_extensions: 'jpg'\n")
    full = parse_scan_extensions(DEFAULTS["scan_extensions"])
    assert _cli_extensions(tmp_path, monkeypatch) == full
    assert _cli_extensions(tmp_path, monkeypatch, "--config", str(cfg)) == ["jpg"]
    explicit = _cli_extensions(tmp_path, monkeypatch, "--extensions=nef", "--config", str(cfg))
    assert explicit == ["nef"], "an explicit --extensions still wins"


def test_a_live_photo_motion_clip_is_not_imported_as_a_video():
    """iPhone Live Photos export as IMG_4214.HEIC + IMG_4214.MOV: the clip
    is the photo's motion, skipped like the ``_1`` sidecars (import toggle
    off), not shown as a video of its own. A video without a same-named
    photo is a video."""
    from bpp.db.live_photo_scan import filter_sidecar_paths

    paths = [
        "/card/IMG_4214.HEIC",
        "/card/IMG_4214.MOV",
        "/card/IMG_4215.JPG",
        "/card/IMG_4215.mov",
        "/card/IMG_4216.MOV",
        "/other/IMG_4214.MOV",
    ]
    keep, skipped = filter_sidecar_paths(paths)
    assert skipped == ["/card/IMG_4214.MOV", "/card/IMG_4215.mov"]
    assert keep == [
        "/card/IMG_4214.HEIC",
        "/card/IMG_4215.JPG",
        "/card/IMG_4216.MOV",
        "/other/IMG_4214.MOV",
    ]
