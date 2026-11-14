"""Record what PyInstaller actually packed into the frozen sidecar.

Used by ``third_party_inventory.py bundled``. The sidecar is the
``bpp-server.app`` helper app (a one-folder build): its files are read
from the bundle on disk and its Python modules from the archive inside
the executable (PyInstaller's own reader, so it must run in the build
environment). A one-file binary (up to 0.2.3) is still read from its
archive alone. Every packed file and module is attributed to its owner:

* a distribution, via the files its RECORD lists or the top-level
  import names it provides;
* the CPython runtime (the Python shared library, ``lib-dynload``
  extension modules, standard-library modules, and any library found
  under the interpreter's own prefix);
* PyInstaller (its bootstrap modules and run-time hooks);
* bppicker itself.

Anything else is reported as unattributed rather than guessed.
Distributions PyInstaller collected that the dependency closure didn't
list (e.g. setuptools, whose ``pkg_resources`` a run-time hook pulls
in) are appended to the inventory's distribution list.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import os
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

CPYTHON = "CPython"
PYINSTALLER = "PyInstaller"
_PYI_PREFIXES = ("pyimod", "pyiboot", "pyi_rth_", "_pyi_rth_utils", "struct")
# typecodes: b=binary, x=data, n=symlink, z=PYZ, m/s=bootstrap module/script
_FILE_TYPES = {"b", "x"}


def _runtime_lib_names() -> set[str]:
    names: set[str] = set()
    for base in {Path(sys.base_prefix), Path(sys.prefix)}:
        lib = base / "lib"
        if lib.is_dir():
            names.update(p.name for p in lib.glob("*.dylib"))
            names.update(p.name for p in lib.glob("*.so*"))
    return names


class Attributor:
    def __init__(self, dists: Mapping[str, md.Distribution], root: str):
        self.root = root
        # A path can belong to several distributions (opencv-python and
        # opencv-python-headless both install cv2/); credit every claimant.
        self.by_file: dict[str, list[str]] = {}
        for dist in dists.values():
            name = dist.metadata["Name"]
            for f in dist.files or []:
                self.by_file.setdefault(str(f).replace("\\", "/"), []).append(name)
        self.by_top = {top: names[0] for top, names in md.packages_distributions().items() if names}
        self.dist_info = {
            re.sub(r"[-_.]+", "_", d.metadata["Name"]).lower(): d.metadata["Name"]
            for d in dists.values()
        }
        self.stdlib = set(getattr(sys, "stdlib_module_names", ()))
        self.runtime_libs = _runtime_lib_names()
        self.py_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"

    def file_owners(self, path: str) -> list[str]:
        if path in self.by_file:
            return self.by_file[path]
        owner = self._fallback_owner(path)
        return [owner] if owner else []

    def _fallback_owner(self, path: str) -> str | None:
        top = path.split("/", 1)[0]
        if top.startswith("Python.framework") or top in {self.py_dir, "base_library.zip"}:
            return CPYTHON
        if re.match(r"libpython3[\d.]*\.(dylib|so)", top) or top in self.runtime_libs:
            return CPYTHON
        m = re.match(r"(.+?)-[^-]+\.dist-info$", top)
        if m:
            return self.dist_info.get(re.sub(r"[-_.]+", "_", m.group(1)).lower())
        return self.module_owner(top.split(".", 1)[0])

    def module_owner(self, module: str) -> str | None:
        top = module.split(".", 1)[0]
        if top == "bpp":
            return self.root
        if top in self.by_top:
            return self.by_top[top]
        if top in self.stdlib or top.startswith("_sysconfigdata"):
            return CPYTHON
        if top.startswith(_PYI_PREFIXES):
            return PYINSTALLER
        return None


def read_archive(binary: Path) -> tuple[dict, list[str]]:
    """(CArchive TOC, module names in the embedded PYZ)."""
    from PyInstaller.archive.readers import CArchiveReader

    reader = CArchiveReader(str(binary))
    modules: list[str] = []
    for name, entry in reader.toc.items():
        if entry[-1] == "z":
            modules.extend(reader.open_embedded_archive(name).toc)
    return reader.toc, modules


_MACHO_MAGIC = {
    bytes.fromhex(m)
    for m in ("feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca")
}


def _is_binary(path: Path) -> bool:
    with path.open("rb") as fh:
        head = fh.read(4)
    return head in _MACHO_MAGIC or head == b"\x7fELF"


def bundle_roots(app: Path) -> list[Path]:
    """Where a helper app's packed files live: PyInstaller puts binaries
    in Contents/Frameworks and data in Contents/Resources, each relative
    to the same root (sys._MEIPASS), with symlinks between the two."""
    return [app / "Contents" / "Frameworks", app / "Contents" / "Resources"]


def read_bundle(app: Path) -> tuple[dict, list[str]]:
    """(TOC-shaped entries for every packed file plus the executable's own
    archive entries, module names in the embedded PYZ) for a one-folder
    helper app. Symlinks are aliases of a file listed once; the app's own
    icon is bpp's and is skipped. PyInstaller renames a dotted folder in
    Contents/Frameworks (``python3.11``, ``cv2/.dylibs``) to ``__dot__``
    and links the original name to it; entries keep the original name,
    the one the running server sees and the distributions record (see
    ``_original_rel``)."""
    import plistlib

    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    exe = app / "Contents" / "MacOS" / info["CFBundleExecutable"]
    toc, modules = read_archive(exe)
    toc = dict(toc)
    icon = info.get("CFBundleIconFile")
    for root in bundle_roots(app):
        for path in _walk(root):
            if path.is_symlink() or not path.is_file():
                continue
            rel = _original_rel(root, path)
            if root.name == "Resources" and icon and rel == icon:
                continue
            toc.setdefault(rel, ("b" if _is_binary(path) else "x",))
    return toc, modules


def _original_rel(root: Path, path: Path) -> str:
    """*path* relative to *root*, with each Frameworks folder PyInstaller
    renamed (``.`` → ``__dot__``) given back its original name. A folder
    counts as renamed only when its parent holds PyInstaller's link from
    the original name to it; file names, Resources paths and a literal
    ``__dot__`` without that link are kept as they are."""
    parts = list(path.relative_to(root).parts)
    if root.name != "Frameworks":
        return "/".join(parts)
    base = root
    for i, part in enumerate(parts[:-1]):
        here = base / part
        if "__dot__" in part:
            alias = base / part.replace("__dot__", ".")
            if alias.is_symlink() and Path(os.readlink(alias)) == Path(part):
                parts[i] = alias.name
        base = here
    return "/".join(parts)


def _walk(root: Path) -> list[Path]:
    """Every entry under *root*, sorted, without following symlinks (a
    linked folder is listed as the link, not walked twice)."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        out.extend(base / n for n in dirnames + filenames)
    return sorted(out)


def tree_sha256(app: Path) -> str:
    """One digest over every file and link of the helper app (path +
    content or target), so the inventory names exactly the build it
    describes."""
    h = hashlib.sha256()
    for path in _walk(app):
        rel = path.relative_to(app).as_posix()
        if path.is_symlink():
            h.update(f"L {rel} -> {path.readlink()}\n".encode())
        elif path.is_file():
            h.update(f"F {rel} {_sha256(path)}\n".encode())
    return h.hexdigest()


def attribute(toc: Mapping[str, tuple], modules: Iterable[str], attributor: Attributor) -> dict:
    owners: dict[str, dict] = {}
    unattributed: list[str] = []

    def bucket(owner: str) -> dict:
        return owners.setdefault(owner, {"files": 0, "binaries": [], "modules": 0})

    for path, entry in toc.items():
        kind = entry[-1]
        if kind in {"m", "s"}:
            # Bootstrap modules and run-time hooks are PyInstaller's; the one
            # remaining script is the frozen entry point (bpp/cli.py).
            pyi = path.startswith(_PYI_PREFIXES)
            bucket(PYINSTALLER if pyi else attributor.root)["modules"] += 1
            continue
        if kind not in _FILE_TYPES:
            continue
        owners_of = attributor.file_owners(path)
        if not owners_of:
            unattributed.append(path)
        for owner in owners_of:
            b = bucket(owner)
            b["files"] += 1
            if kind == "b":
                b["binaries"].append(path)
    for module in modules:
        owner = attributor.module_owner(module)
        if owner is None:
            unattributed.append(f"module {module}")
        else:
            bucket(owner)["modules"] += 1
    for b in owners.values():
        b["binaries"].sort()
    return {"owners": dict(sorted(owners.items())), "unattributed": sorted(unattributed)}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def record_bundled(inv: dict, binary: Path, dists: Mapping[str, md.Distribution]) -> None:
    from third_party_inventory import canon, dist_record

    is_bundle = binary.is_dir()
    toc, modules = read_bundle(binary) if is_bundle else read_archive(binary)
    result = attribute(toc, modules, Attributor(dists, inv["root"]["name"]))
    listed = {canon(d["name"]) for d in inv["distributions"]}
    special = {CPYTHON, PYINSTALLER, inv["root"]["name"]}
    added = []
    for owner in result["owners"]:
        key = canon(owner)
        if owner in special or key in listed or key not in dists:
            continue
        rec = dist_record(dists[key], ["(collected by PyInstaller)"])
        inv["distributions"].append(rec)
        added.append(rec["name"])
    present = {canon(o) for o in result["owners"]}
    for rec in inv["distributions"]:
        # False = installed in the build environment but nothing of it was
        # packed into the sidecar (e.g. a module excluded from the build).
        rec["in_sidecar"] = canon(rec["name"]) in present
    inv["distributions"].sort(key=lambda r: r["name"].lower())
    inv["sidecar"] = {
        "binary": binary.name,
        "layout": "one-folder helper app" if is_bundle else "one-file binary",
        "sha256": tree_sha256(binary) if is_bundle else _sha256(binary),
        "archive_entries": len(toc),
        "pyz_modules": len(modules),
        "added_by_pyinstaller": sorted(added),
        **result,
    }
