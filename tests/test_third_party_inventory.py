"""scripts/third_party_inventory.py (+ its sources / bundled helpers) — offline parts."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import urllib.error
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


inv_mod = _load("third_party_inventory")
src_mod = _load("third_party_sources")
bnd_mod = _load("third_party_bundled")

APACHE_TEXT = """
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/
"""


def _dist(site: Path, name: str, version: str, meta: list[str], files: dict[str, str]) -> None:
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        "\n".join(["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}", *meta]) + "\n"
    )
    record = []
    for rel, body in files.items():
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        record.append(f"{rel},,")
    record.append(f"{info.name}/METADATA,,")
    (info / "RECORD").write_text("\n".join(record) + "\n")


@pytest.fixture
def site(tmp_path: Path) -> Path:
    s = tmp_path / "site-packages"
    _dist(
        s,
        "bppicker",
        "1.0",
        [
            "Requires-Dist: numpy>=1.24",
            'Requires-Dist: rawpy>=0.21; extra == "raw"',
            'Requires-Dist: pytest>=7; extra == "dev"',
            'Requires-Dist: bppicker[raw]; extra == "all"',
            'Requires-Dist: tomli; python_version < "3.0"',
        ],
        {"bpp/__init__.py": ""},
    )
    _dist(
        s,
        "numpy",
        "2.0",
        ["License-Expression: BSD-3-Clause"],
        {
            "numpy/__init__.py": "",
            "numpy/_core/_multiarray_umath.cpython-311-darwin.so": "",
            "numpy/.dylibs/libopenblas.dylib": "",
        },
    )
    _dist(
        s,
        "rawpy",
        "0.27",
        [
            "Classifier: License :: OSI Approved :: MIT License",
            "Requires-Dist: numpy",
            "Requires-Dist: extra-thing[fancy]",
        ],
        {
            "rawpy/__init__.py": "",
            "rawpy/_rawpy.cpython-311-darwin.so": "",
            "rawpy/libraw_r.25.dylib": "",  # NOT under *.dylibs — the LGPL LibRaw
            "rawpy/.dylibs/libjpeg.8.dylib": "",
            "rawpy/libfoo.so.3": "",
        },
    )
    _dist(
        s,
        "extra-thing",
        "1.0",
        [
            "License: MIT",
            'Requires-Dist: fancydep; extra == "fancy"',
            'Requires-Dist: unused; extra == "x"',
        ],
        {"extra_thing/__init__.py": ""},
    )
    _dist(
        s,
        "fancydep",
        "1.0",
        [],
        {"fancydep/__init__.py": "", "fancydep-1.0.dist-info/LICENSE": APACHE_TEXT},
    )
    _dist(
        s, "mystery", "1.0", ["Home-page: https://example.org/mystery"], {"mystery/__init__.py": ""}
    )
    for tool in ("pytest", "pip", "setuptools", "pyinstaller", "tomli"):
        _dist(s, tool, "1.0", ["License: MIT"], {f"{tool}/__init__.py": ""})
    return s


def _inventory(site: Path, extras=("raw",)) -> dict:
    return inv_mod.build_inventory(extras=extras, search_path=[str(site)])


def _by_name(inv: dict) -> dict:
    return {d["name"]: d for d in inv["distributions"]}


# ── dependency closure ───────────────────────────────────────────────


def test_closure_follows_extras_and_skips_build_tools(site):
    dists = _by_name(_inventory(site))
    assert set(dists) == {"numpy", "rawpy", "extra-thing", "fancydep"}
    assert dists["rawpy"]["required_by"] == ["bppicker[raw]"]
    assert dists["fancydep"]["required_by"] == ["extra-thing[fancy]"]
    assert sorted(dists["numpy"]["required_by"]) == ["bppicker", "rawpy"]


def test_closure_without_the_extra_drops_its_requirements(site):
    assert set(_by_name(_inventory(site, extras=()))) == {"numpy"}


def test_self_referencing_extra_is_expanded(site):
    assert "rawpy" in _by_name(_inventory(site, extras=("all",)))


def test_closure_reports_declared_but_missing(site):
    inv = _inventory(site, extras=("raw",))
    assert inv["missing_requirements"] == []
    shutil.rmtree(site / "fancydep-1.0.dist-info")
    missing = _inventory(site)["missing_requirements"]
    assert len(missing) == 1 and missing[0].startswith("fancydep"), missing


def test_fallback_requirement_parser_handles_extras_conservatively(monkeypatch):
    monkeypatch.setattr(inv_mod, "_Req", None)
    name, extras, applies = inv_mod.parse_requirement('qrcode[pil] >=7.0 ; extra == "web"')
    assert (name, extras) == ("qrcode", {"pil"})
    assert applies("web") and not applies("")
    _, _, base = inv_mod.parse_requirement('tomli; python_version < "3.11"')
    assert base("")  # non-extra markers are kept: over-inclusion only


def test_missing_root_fails_loudly(tmp_path):
    with pytest.raises(SystemExit, match="bppicker is not installed"):
        inv_mod.build_inventory(search_path=[str(tmp_path)])


# ── native libraries ─────────────────────────────────────────────────


def test_native_libraries_found_wherever_record_puts_them(site):
    rawpy = _by_name(_inventory(site))["rawpy"]
    assert rawpy["native_libraries"] == [
        "rawpy/.dylibs/libjpeg.8.dylib",
        "rawpy/libfoo.so.3",
        "rawpy/libraw_r.25.dylib",
    ]
    assert rawpy["extension_modules"] == ["rawpy/_rawpy.cpython-311-darwin.so"]


# ── licenses ─────────────────────────────────────────────────────────


def test_license_sources_in_priority_order(site):
    dists = _by_name(_inventory(site))
    assert (dists["numpy"]["license"], dists["numpy"]["license_source"]) == (
        "BSD-3-Clause",
        "License-Expression",
    )
    assert (dists["rawpy"]["license"], dists["rawpy"]["license_source"]) == ("MIT", "classifier")
    assert dists["extra-thing"]["license_source"] == "License field"
    fancy = dists["fancydep"]
    assert fancy["license"] == "Apache-2.0", fancy
    assert fancy["license_source"].startswith("license file text"), fancy


def test_unknown_license_points_at_homepage(site):
    import importlib.metadata as md

    dist = next(md.distributions(path=[str(site)], name="mystery"))
    assert inv_mod.license_of(dist) == ("UNKNOWN (see https://example.org/mystery)", "none found")


@pytest.mark.parametrize(
    ("text", "spdx"),
    [
        ("GNU LESSER GENERAL PUBLIC LICENSE\nVersion 2.1, February 1999", "LGPL-2.1"),
        ("GNU LESSER GENERAL PUBLIC LICENSE Version 3, 29 June 2007", "LGPL-3.0"),
        ("GNU GENERAL PUBLIC LICENSE\n Version 2, June 1991", "GPL-2.0"),
        ("GNU AFFERO GENERAL PUBLIC LICENSE Version 3", "AGPL-3.0"),
        ("Permission is hereby granted, free of charge, to any person", "MIT"),
        (
            "Redistribution and use in source and binary forms ... Neither the name of",
            "BSD-3-Clause",
        ),
        ("Redistribution and use in source and binary forms, with or without", "BSD-2-Clause"),
        ("Boost Software License - Version 1.0", "BSL-1.0"),
        ("CC0 1.0 Universal\n\nStatement of Purpose", "CC0-1.0"),
        ("All rights reserved.", None),
    ],
)
def test_license_text_heuristics(text, spdx):
    assert inv_mod.license_from_text(text) == spdx


def test_runtime_and_bootloader_are_recorded(site):
    inv = _inventory(site)
    assert inv["python_runtime"]["license"] == "PSF-2.0"
    assert inv["python_runtime"]["version"] == ".".join(map(str, sys.version_info[:3]))
    assert "Bootloader-exception" in inv["pyinstaller"]["license"]
    assert "Rust" in inv["scope"]


# ── sources ──────────────────────────────────────────────────────────

RECIPE = """
LIBX265_URL = "https://example.org/x265_4.1.tar.gz"
def build_lib(url, name):
    script_dir = path.dirname(path.abspath(__file__))
    linux_dir = path.join(script_dir, "linux")
    if name == "x265":
        run(f"patch -p 1 -i {path.join(script_dir, 'x265_cmake_1.patch')}".split())
        run(f"patch -p 1 -i {path.join(script_dir, 'x265_cmake_2.patch')}".split())
    elif name == "libde265":
        for patch in ("libde265/fix.patch",):
            run(["patch", "-i", path.join(linux_dir, patch)])
    # elif name == "libheif":
    #     for patch in ("libheif/001-void-unused-variable.patch",):
"""


def test_recipe_patches_resolved_and_comments_ignored():
    assert src_mod.recipe_patches(RECIPE) == [
        "libheif/linux/libde265/fix.patch",
        "libheif/x265_cmake_1.patch",
        "libheif/x265_cmake_2.patch",
    ]
    assert src_mod.recipe_archives(RECIPE) == {"LIBX265": "https://example.org/x265_4.1.tar.gz"}


def test_downloads_retry_with_backoff_then_succeed():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("connection reset")
        return b"ok"

    assert src_mod.with_retries(flaky, "x", sleep=sleeps.append) == b"ok"
    assert sleeps == [2, 4]


def test_downloads_give_up_after_three_tries():
    def down():
        raise urllib.error.HTTPError("u", 503, "busy", {}, None)

    with pytest.raises(src_mod.SourceFetchError, match="HTTP 503"):
        src_mod.with_retries(down, "x", sleep=lambda _s: None)


def test_missing_file_is_not_retried():
    calls = []

    def gone():
        calls.append(1)
        raise urllib.error.HTTPError("u", 404, "missing", {}, None)

    with pytest.raises(src_mod.SourceFetchError, match="HTTP 404"):
        src_mod.with_retries(gone, "x", sleep=lambda _s: None)
    assert len(calls) == 1


# ── what PyInstaller bundled ─────────────────────────────────────────


def test_bundled_files_attributed_to_dist_runtime_or_pyinstaller(site):
    import importlib.metadata as md

    dists = {inv_mod.canon(d.metadata["Name"]): d for d in md.distributions(path=[str(site)])}
    att = bnd_mod.Attributor(dists, "bppicker")
    att.by_file.setdefault("cv2/shared.so", []).extend(["opencv-a", "opencv-b"])
    toc = {
        "pyiboot01_bootstrap": (0, 0, 0, 1, "s"),
        "cli": (0, 0, 0, 1, "s"),
        "rawpy/libraw_r.25.dylib": (0, 0, 0, 1, "b"),
        "Python.framework/Versions/3.11/Python": (0, 0, 0, 1, "b"),
        f"python{sys.version_info.major}.{sys.version_info.minor}/lib-dynload/_ssl.so": (
            0,
            0,
            0,
            1,
            "b",
        ),
        "numpy-2.0.dist-info/METADATA": (0, 0, 0, 1, "x"),
        "cv2/shared.so": (0, 0, 0, 1, "b"),
        "libmystery.1.dylib": (0, 0, 0, 1, "b"),
        "rawpy/alias.dylib": (0, 0, 0, 1, "n"),
    }
    result = bnd_mod.attribute(toc, ["json", "bpp.cli", "zz_unknown_mod"], att)
    owners = result["owners"]
    assert owners["rawpy"]["binaries"] == ["rawpy/libraw_r.25.dylib"]
    assert owners["CPython"]["files"] == 2 and owners["CPython"]["modules"] == 1
    assert owners["PyInstaller"]["modules"] == 1
    assert owners["bppicker"]["modules"] == 2  # entry script + bpp.cli
    assert owners["numpy"]["files"] == 1
    assert owners["opencv-a"]["files"] == owners["opencv-b"]["files"] == 1
    assert result["unattributed"] == ["libmystery.1.dylib", "module zz_unknown_mod"]


# ── workflow / build wiring ──────────────────────────────────────────


def _repo_file(rel: str) -> Path:
    path = ROOT / rel
    if not path.exists():  # the sdist leaves out .github/ and desktop/
        pytest.skip(f"{rel} not in this checkout")
    return path


def test_workflow_extras_and_asset_names_match_script_and_notice():
    wf = yaml.safe_load(_repo_file(".github/workflows/release-desktop.yml").read_text())
    env = wf["jobs"]["build-macos-app"]["env"]
    assert env["BPP_EXTRAS"] == ",".join(inv_mod.DESKTOP_EXTRAS)
    notice = (ROOT / "NOTICE.txt").read_text()
    for asset in (env["INVENTORY_ASSET"], env["SOURCES_ASSET"]):
        assert asset in notice, asset


def test_sidecar_build_collects_files_import_analysis_misses():
    script = _repo_file("desktop/build-sidecar.sh").read_text()
    assert '--collect-binaries "mediapipe"' in script  # ctypes-loaded libmediapipe
    assert ":cv2/data" in script  # Haar cascade for the last-resort detector
    for mod in ("face_recognition", "face_recognition_models", "dlib"):
        assert f'--exclude-module "{mod}"' in script, mod


def test_download_refuses_a_dest_outside_the_output_folder(tmp_path, monkeypatch):
    """A patch's ``dest`` is a string literal read out of the upstream
    recipe; ``../`` in it must not write outside the sources folder."""
    fetched = []
    monkeypatch.setattr(src_mod, "fetch", lambda url: fetched.append(url) or b"data")
    out = tmp_path / "third-party"
    bad = {"component": "x", "version": "1", "url": "u", "kind": "patch", "dest": "../x.patch"}
    fine = {**bad, "url": "first", "dest": "libheif/linux/first.patch"}
    with pytest.raises(src_mod.SourceFetchError, match="outside"):
        src_mod.download([fine, bad], out)
    assert not (tmp_path / "x.patch").exists()
    assert fetched == [], "a refused plan must not fetch anything first"
    for folder_itself in ("", "."):
        with pytest.raises(src_mod.SourceFetchError, match="outside"):
            src_mod.download([{**bad, "dest": folder_itself}], out)
    good = {**bad, "dest": "libheif/linux/x.patch"}
    src_mod.download([good], out)
    assert (out / "libheif/linux/x.patch").read_bytes() == b"data"
