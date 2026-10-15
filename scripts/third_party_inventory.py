#!/usr/bin/env python3
"""Inventory the third-party code in the desktop app's Python sidecar.

Run inside the environment the sidecar is frozen from (the release
workflow does this right after installing bpp + extras). Three steps,
each its own subcommand so a network failure in one can't take the
others down:

``inventory`` (offline) writes ``<out>/third-party-inventory.json``:

* every distribution in the dependency closure of ``bppicker`` with
  the extras the desktop build installs (name, version, license and
  where that license was read from, homepage, which requirement
  pulled it in), plus every shared library (``.dylib`` / ``.so`` /
  ``.so.N`` / ``.dll``) its RECORD lists, wherever it sits. Build-only
  tools (pip, PyInstaller, pytest, ...) are outside the closure and
  are not listed;
* the Python runtime the sidecar embeds (CPython, PSF-2.0) and the
  library versions it reports (OpenSSL, SQLite, zlib, expat);
* the PyInstaller bootloader the build embeds (GPL-2.0-or-later WITH
  Bootloader-exception).

``sources`` (network) downloads the exact sources the HEIC and RAW
wheels were compiled from into ``<out>/third-party-sources/``: the
x265 / libde265 / libheif archives named in pillow-heif's build recipe
at the installed tag, that recipe, every patch it applies and the
wheel workflow that runs it; the LibRaw submodule commits rawpy pins
at the installed tag plus rawpy's build scripts. Every download is
retried; anything that still can't be fetched fails the command.

``bundled`` (offline, after PyInstaller) reads the frozen sidecar's
archive and records what was actually packed into it, attributed to
the distribution, the Python runtime or PyInstaller, and adds any
distribution PyInstaller collected that the closure didn't list.

The Tauri (Rust) shell of the app is not covered by this inventory.

Usage::

    python scripts/third_party_inventory.py inventory --out third-party
    python scripts/third_party_inventory.py sources --out third-party
    python scripts/third_party_inventory.py bundled --out third-party --binary dist/bpp-server
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import platform
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = "bppicker"
DESKTOP_EXTRAS = ("web", "heic", "faces", "raw", "inpaint")
INVENTORY_NAME = "third-party-inventory.json"
SOURCES_DIR = "third-party-sources"
SCOPE = (
    "The Python sidecar of the desktop app: the CPython runtime, every Python "
    "distribution in the dependency closure of bppicker with the desktop extras, "
    "the shared libraries those distributions install, and the PyInstaller "
    "bootloader. The Tauri (Rust) shell of the app is not covered."
)

# ── names, requirements, closure ─────────────────────────────────────


def canon(name: str) -> str:
    """PEP 503 normalized project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


try:  # packaging is almost always present; pip vendors a copy as a fallback
    from packaging.requirements import Requirement as _Req
except ImportError:  # pragma: no cover - exercised only without packaging
    try:
        from pip._vendor.packaging.requirements import Requirement as _Req
    except ImportError:
        _Req = None

_EXTRA_RE = re.compile(r"""extra\s*==\s*['"]([^'"]+)['"]""")


def parse_requirement(spec: str) -> tuple[str, set[str], Callable[[str], bool]]:
    """Return (name, extras, applies(extra)) for one Requires-Dist line."""
    if _Req is not None:
        req = _Req(spec)
        marker = req.marker

        def applies(extra: str) -> bool:
            return marker is None or marker.evaluate({"extra": extra})

        return req.name, set(req.extras), applies
    # Conservative fallback: honour `extra == "x"` clauses exactly and treat
    # every other marker as true. That can only over-include, and only
    # distributions that are actually installed end up in the inventory.
    head, _, marker_text = spec.partition(";")
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?", head)
    if not m:
        raise ValueError(f"unparseable requirement: {spec!r}")
    extras = {e.strip() for e in (m.group(2) or "").split(",") if e.strip()}
    needed = set(_EXTRA_RE.findall(marker_text))

    def applies(extra: str) -> bool:
        return not needed or extra in needed

    return m.group(1), extras, applies


def dependency_closure(
    dists: dict[str, md.Distribution], root: str, extras: Iterable[str]
) -> tuple[dict[str, set[str]], list[str]]:
    """Walk Requires-Dist from ``root[extras]``.

    Returns ({canonical name: {who required it}}, [missing requirements]).
    The root itself is not included in the result.
    """
    required_by: dict[str, set[str]] = {}
    missing: list[str] = []
    seen: set[tuple[str, str]] = set()
    queue = [(canon(root), ""), *((canon(root), e) for e in extras)]
    while queue:
        name, extra = queue.pop()
        if (name, extra) in seen:
            continue
        seen.add((name, extra))
        dist = dists.get(name)
        if dist is None:
            continue
        parent = dist.metadata["Name"] + (f"[{extra}]" if extra else "")
        for spec in dist.requires or []:
            dep, dep_extras, applies = parse_requirement(spec)
            # Each extra pass only adds what that extra adds; the base
            # requirements were handled by the "" pass.
            if not applies(extra) or (extra and applies("")):
                continue
            key = canon(dep)
            if key not in dists:
                missing.append(f"{spec} (required by {parent})")
                continue
            if key != canon(root):
                required_by.setdefault(key, set()).add(parent)
            queue.append((key, ""))
            queue.extend((key, e) for e in dep_extras)
    return required_by, sorted(set(missing))


# ── licenses ─────────────────────────────────────────────────────────

_CLASSIFIER_SPDX = {
    "Apache Software License": "Apache-2.0",
    "MIT License": "MIT",
    "MIT No Attribution License (MIT-0)": "MIT-0",
    "BSD License": "BSD",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Boost Software License 1.0 (BSL-1.0)": "BSL-1.0",
    "Historical Permission Notice and Disclaimer (HPND)": "HPND",
    "zlib/libpng License": "Zlib",
    "The Unlicense (Unlicense)": "Unlicense",
    "Public Domain": "Public Domain",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0",
    "GNU General Public License v3 or later (GPLv3+)": "GPL-3.0-or-later",
    "GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.0",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.0-or-later",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "GNU Affero General Public License v3": "AGPL-3.0",
}

# Ordered: the first match wins, so the more specific texts come first.
_TEXT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AGPL-3.0", ("GNU AFFERO GENERAL PUBLIC LICENSE",)),
    ("LGPL-3.0", ("GNU LESSER GENERAL PUBLIC LICENSE", "Version 3")),
    ("LGPL-2.1", ("GNU LESSER GENERAL PUBLIC LICENSE", "Version 2.1")),
    ("GPL-3.0", ("GNU GENERAL PUBLIC LICENSE", "Version 3")),
    ("GPL-2.0", ("GNU GENERAL PUBLIC LICENSE", "Version 2")),
    ("Apache-2.0", ("Apache License", "Version 2.0")),
    ("MPL-2.0", ("Mozilla Public License", "2.0")),
    ("BSL-1.0", ("Boost Software License",)),
    ("CC0-1.0", ("CC0 1.0 Universal",)),
    ("MIT", ("Permission is hereby granted, free of charge",)),
    ("BSD-3-Clause", ("Redistribution and use in source and binary forms", "Neither the name")),
    ("BSD-2-Clause", ("Redistribution and use in source and binary forms",)),
    ("ISC", ("Permission to use, copy, modify, and/or distribute this software",)),
)

_LICENSE_FILE_RE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE)[^/]*$", re.I)


def license_from_text(text: str) -> str | None:
    """Best-effort SPDX id from the head of a license text."""
    head = " ".join(text[:4000].split())
    for spdx, needles in _TEXT_RULES:
        if all(n.lower() in head.lower() for n in needles):
            return spdx
    return None


def _license_files(dist: md.Distribution) -> list[str]:
    files = [str(f) for f in dist.files or []]
    picked = [f for f in files if _LICENSE_FILE_RE.search(f) and ".dist-info/" in f]
    return picked or [f for f in files if _LICENSE_FILE_RE.search(f)]


def license_of(dist: md.Distribution) -> tuple[str, str]:
    """(license, where it was read from) — metadata first, then license files."""
    meta = dist.metadata
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr, "License-Expression"
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    named = [c.split(" :: ")[-1] for c in classifiers if c.count(" :: ") >= 2]
    if named:
        return " OR ".join(_CLASSIFIER_SPDX.get(c, c) for c in named), "classifier"
    raw = (meta.get("License") or "").strip()
    if raw and raw.upper() != "UNKNOWN":
        if "\n" not in raw and len(raw) <= 120:
            return raw, "License field"
        guess = license_from_text(raw)
        if guess:
            return guess, "License field text"
    for rel in _license_files(dist):
        try:
            text = dist.read_text(rel) or ""
        except (OSError, UnicodeDecodeError):
            text = ""
        if not text:
            located = Path(str(dist.locate_file(rel)))
            text = located.read_text(errors="replace") if located.is_file() else ""
        guess = license_from_text(text)
        if guess:
            return guess, f"license file text ({rel})"
    home = homepage_of(dist)
    return (f"UNKNOWN (see {home})" if home else "UNKNOWN"), "none found"


def homepage_of(dist: md.Distribution) -> str:
    meta = dist.metadata
    if meta.get("Home-page"):
        return meta["Home-page"]
    for url in meta.get_all("Project-URL") or []:
        label, _, link = url.partition(",")
        if label.strip().lower() in {"homepage", "home", "source", "repository", "source code"}:
            return link.strip()
    return ""


# ── native libraries ─────────────────────────────────────────────────

_SHARED_LIB_RE = re.compile(r"\.(dylib|dll|pyd|so(\.\d+)*)$")
_EXTENSION_RE = re.compile(r"\.(cpython-[^/]*|abi3|pypy[^/]*)\.so$|\.pyd$")


def shared_libraries(dist: md.Distribution) -> tuple[list[str], list[str]]:
    """(native libraries, Python extension modules) from the dist's RECORD."""
    libs, exts = set(), set()
    for f in dist.files or []:
        rel = str(f).replace("\\", "/")
        if not _SHARED_LIB_RE.search(rel):
            continue
        (exts if _EXTENSION_RE.search(rel) else libs).add(rel)
    return sorted(libs), sorted(exts)


# ── runtime + bootloader entries ─────────────────────────────────────


def python_runtime() -> dict:
    """The CPython runtime PyInstaller embeds, with the library versions
    it reports. See https://docs.python.org/3/license.html for the full
    list of third-party code a CPython build incorporates."""
    reported = []
    probes = (
        ("OpenSSL", "ssl", "OPENSSL_VERSION", None),
        ("SQLite", "sqlite3", "sqlite_version", "blessing (public domain)"),
        ("zlib", "zlib", "ZLIB_RUNTIME_VERSION", "Zlib"),
        ("expat", "pyexpat", "EXPAT_VERSION", "MIT"),
    )
    for name, module, attr, lic in probes:
        try:
            value = str(getattr(__import__(module), attr))
        except (ImportError, AttributeError):
            continue
        if name == "OpenSSL":
            lic = "Apache-2.0" if re.search(r"OpenSSL [3-9]", value) else "OpenSSL"
        reported.append({"name": name, "version": value, "license": lic})
    return {
        "name": "CPython",
        "version": platform.python_version(),
        "license": "PSF-2.0",
        "homepage": "https://www.python.org/",
        "license_details": "https://docs.python.org/3/license.html",
        "reported_libraries": reported,
    }


def pyinstaller_entry() -> dict:
    def ver(name: str) -> str | None:
        try:
            return md.version(name)
        except md.PackageNotFoundError:
            return None

    return {
        "name": "PyInstaller bootloader and run-time hooks",
        "pyinstaller_version": ver("pyinstaller"),
        "pyinstaller_hooks_contrib_version": ver("pyinstaller-hooks-contrib"),
        "license": "GPL-2.0-or-later WITH Bootloader-exception (run-time hooks: Apache-2.0)",
        "homepage": "https://github.com/pyinstaller/pyinstaller",
        "note": "Embedded by the build; PyInstaller itself is a build tool and "
        "is not otherwise distributed.",
    }


# ── inventory ────────────────────────────────────────────────────────


def _index(search_path: list[str] | None) -> dict[str, md.Distribution]:
    out: dict[str, md.Distribution] = {}
    for dist in md.distributions(path=search_path) if search_path else md.distributions():
        name = dist.metadata["Name"]
        if name:
            out.setdefault(canon(name), dist)  # first on the path wins, like import
    return out


def dist_record(dist: md.Distribution, required_by: Iterable[str] = ()) -> dict:
    lic, source = license_of(dist)
    libs, exts = shared_libraries(dist)
    return {
        "name": dist.metadata["Name"],
        "version": dist.version,
        "license": lic,
        "license_source": source,
        "homepage": homepage_of(dist),
        "required_by": sorted(required_by),
        "native_libraries": libs,
        "extension_modules": exts,
    }


def build_inventory(
    *,
    root: str = ROOT,
    extras: Iterable[str] = DESKTOP_EXTRAS,
    search_path: list[str] | None = None,
) -> dict:
    extras = list(extras)
    dists = _index(search_path)
    if canon(root) not in dists:
        raise SystemExit(f"{root} is not installed in this environment")
    required_by, missing = dependency_closure(dists, root, extras)
    records = [dist_record(dists[k], required_by[k]) for k in required_by]
    records.sort(key=lambda r: r["name"].lower())
    return {
        "schema": 2,
        "scope": SCOPE,
        "root": {"name": root, "version": dists[canon(root)].version, "extras": extras},
        "platform": sys.platform,
        "python_runtime": python_runtime(),
        "pyinstaller": pyinstaller_entry(),
        "distributions": records,
        "missing_requirements": missing,
        "pinned_copyleft_sources": [],
    }


def _load(out: Path) -> dict:
    return json.loads((out / INVENTORY_NAME).read_text())


def _save(out: Path, inv: dict) -> None:
    (out / INVENTORY_NAME).write_text(json.dumps(inv, indent=2) + "\n")


def _cmd_inventory(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    extras = [e for e in args.extras.split(",") if e]
    inv = build_inventory(root=args.root, extras=extras)
    _save(args.out, inv)
    n_libs = sum(len(d["native_libraries"]) for d in inv["distributions"])
    print(
        f"{len(inv['distributions'])} distributions in the {args.root}[{args.extras}] "
        f"closure, {n_libs} native libraries; Python {inv['python_runtime']['version']}"
    )
    for line in inv["missing_requirements"]:
        print(f"::warning::declared but not installed: {line}")
    for d in inv["distributions"]:
        if str(d.get("license", "")).startswith("UNKNOWN"):
            print(f"::warning::license not identified for {d['name']}: {d['license']}")
    return 0


def _cmd_sources(args: argparse.Namespace) -> int:
    from third_party_sources import SourceFetchError, fetch_all

    args.out.mkdir(parents=True, exist_ok=True)
    try:
        items = fetch_all(args.out / SOURCES_DIR)
    except SourceFetchError as e:
        print(f"::error::Couldn't collect the third-party sources: {e}")
        return 1
    if (args.out / INVENTORY_NAME).exists():
        inv = _load(args.out)
        inv["pinned_copyleft_sources"] = items
        _save(args.out, inv)
    return 0


def _cmd_bundled(args: argparse.Namespace) -> int:
    from third_party_bundled import record_bundled

    inv = _load(args.out)
    record_bundled(inv, args.binary, _index(None))
    _save(args.out, inv)
    side = inv["sidecar"]
    print(
        f"{side['binary']}: {side['archive_entries']} archive entries, "
        f"{len(side['unattributed'])} unattributed"
    )
    for path in side["unattributed"]:
        print(f"::warning::sidecar file not attributed to any distribution: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inventory", help="write the inventory (offline)")
    p.add_argument("--root", default=ROOT)
    p.add_argument("--extras", default=",".join(DESKTOP_EXTRAS))
    p.set_defaults(func=_cmd_inventory)
    p = sub.add_parser("sources", help="download the pinned HEIC/RAW sources")
    p.set_defaults(func=_cmd_sources)
    p = sub.add_parser("bundled", help="record what PyInstaller packed into the sidecar")
    p.add_argument("--binary", required=True, type=Path)
    p.set_defaults(func=_cmd_bundled)
    for p in sub.choices.values():
        p.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
