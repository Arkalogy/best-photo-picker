"""Fetch the exact sources the HEIC and RAW wheels were compiled from.

Used by ``third_party_inventory.py sources``. Resolves everything from
the *installed* versions:

* pillow-heif: ``libheif/build_libs.py`` at the installed tag names the
  x265 / libde265 / libheif archives it compiles; every ``*.patch`` the
  recipe's code references is fetched from the same tag (comments are
  ignored, since they never run), along with the wheel workflow that
  runs the recipe. The archive versions are cross-checked against the
  versions the installed binary reports.
* rawpy: the LibRaw / LibRaw-cmake submodule commits pinned at the
  installed tag (plus the GPL demosaic packs if the installed build
  enabled them), and rawpy's build scripts at that tag.

Every download is retried with backoff; anything that still fails
raises, so a partial source set is never reported as complete.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata as md
import json
import os
import re
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

GH_RAW = "https://raw.githubusercontent.com"
GH_API = "https://api.github.com"
PILLOW_HEIF_REPO = "bigcat88/pillow_heif"
RAWPY_REPO = "letmaik/rawpy"
RETRIES = 3
_TRANSIENT_HTTP = {403, 408, 425, 429, 500, 502, 503, 504}


class SourceFetchError(RuntimeError):
    pass


def with_retries(fn: Callable[[], bytes], what: str, *, sleep=time.sleep) -> bytes:
    """Run ``fn`` up to RETRIES times, backing off 2 s, 4 s between tries.
    A 404 (or other non-transient HTTP status) fails immediately."""
    for attempt in range(1, RETRIES + 1):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code not in _TRANSIENT_HTTP or attempt == RETRIES:
                raise SourceFetchError(f"{what}: HTTP {e.code}") from e
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == RETRIES:
                raise SourceFetchError(f"{what}: {e}") from e
            err = str(e)
        print(f"  retry {attempt}/{RETRIES - 1} for {what} after: {err}", file=sys.stderr)
        sleep(2**attempt)
    raise AssertionError("unreachable")


def _is_github_api(url: str) -> bool:
    """Exactly https://api.github.com — not a look-alike host
    (``api.github.com.evil.example``) or a path that merely contains it.
    Some URLs come from upstream files (pillow-heif's recipe, rawpy's
    .gitmodules), so a text-prefix check isn't enough."""
    p = urllib.parse.urlsplit(url)
    return p.scheme == "https" and p.hostname == "api.github.com" and not p.username


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never carry the token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not _is_github_api(newurl):
            new.remove_header("Authorization")
        return new


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_SameHostRedirects)


def _get(url: str, timeout: int = 120) -> bytes:
    headers = {"User-Agent": "bppicker-release"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and _is_github_api(url):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with _opener().open(req, timeout=timeout) as resp:
        return resp.read()


def fetch(url: str) -> bytes:
    return with_retries(lambda: _get(url), url)


# ── pillow-heif ──────────────────────────────────────────────────────

# Directory variables the recipe joins patch names onto, relative to the repo.
_RECIPE_DIRS = {"script_dir": "libheif", "linux_dir": "libheif/linux"}


def recipe_patches(recipe: str) -> list[str]:
    """Repo-relative paths of every ``*.patch`` the recipe's *code* uses.

    Reads the AST, so commented-out patch steps are ignored. A patch
    joined onto a known directory variable resolves exactly; any other
    ``*.patch`` literal is resolved relative to ``libheif/linux`` (where
    upstream keeps per-library patches) by the caller's existence check.
    """
    tree = ast.parse(recipe)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", "")) == "join"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and str(node.args[1].value).endswith(".patch")
        ):
            base = getattr(node.args[0], "id", "")
            name = node.args[1].value
            found[name] = f"{_RECIPE_DIRS.get(base, 'libheif')}/{name}"
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.endswith(".patch")
            and " " not in node.value
        ):
            found.setdefault(node.value, f"libheif/linux/{node.value}")
    return sorted(set(found.values()))


def recipe_archives(recipe: str) -> dict[str, str]:
    return dict(re.findall(r'^(LIB\w+)_URL\s*=\s*"([^"]+)"', recipe, re.M))


def _version_in(url: str) -> str:
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)\.tar", url) or re.search(r"/v?(\d+\.\d+(?:\.\d+)?)/", url)
    return m.group(1) if m else "unknown"


def pillow_heif_plan() -> list[dict]:
    try:
        import pillow_heif
    except ImportError as e:
        # The release ships its LGPL/GPL builds: their sources must ship too.
        raise SourceFetchError(
            "pillow-heif is not installed in the build environment, so its "
            "LGPL/GPL sources can't be collected (install the heic extra)"
        ) from e
    version = md.version("pillow-heif")
    tag = f"v{version}"
    base = f"{GH_RAW}/{PILLOW_HEIF_REPO}/{tag}"
    recipe = fetch(f"{base}/libheif/build_libs.py").decode()
    reported = json.dumps(pillow_heif.libheif_info())
    build_dir = f"pillow_heif-{tag}-build"
    plan = []
    for rel, lic in (
        ("libheif/build_libs.py", "BSD-3-Clause"),
        (".github/workflows/wheels.yml", "BSD-3-Clause"),
        *((p, "same as the library it patches") for p in recipe_patches(recipe)),
    ):
        plan.append(
            {
                "component": f"pillow-heif build: {rel}",
                "version": tag,
                "license": lic,
                "url": f"{base}/{rel}",
                "kind": "file",
                "dest": f"{build_dir}/{rel}",
            }
        )
    archives = recipe_archives(recipe)
    for key, name, lic in (
        ("LIBX265", "x265", "GPL-2.0-or-later"),
        ("LIBDE265", "libde265", "LGPL-3.0-or-later"),
        ("LIBHEIF", "libheif", "LGPL-3.0-or-later"),
    ):
        url = archives.get(key)
        if not url:
            raise SourceFetchError(f"pillow-heif {tag} recipe has no {key}_URL")
        v = _version_in(url)
        if v not in reported:
            raise SourceFetchError(
                f"{key} {v} from the {tag} recipe doesn't match the installed binary: {reported}"
            )
        plan.append(
            {
                "component": name,
                "version": v,
                "license": lic,
                "url": url,
                "kind": "archive",
                "dest": f"{name}-{v}.tar.gz",
            }
        )
    return plan


# ── rawpy ────────────────────────────────────────────────────────────


def rawpy_plan() -> list[dict]:
    try:
        import rawpy
    except ImportError as e:
        raise SourceFetchError(
            "rawpy is not installed in the build environment, so the LibRaw "
            "sources can't be collected (install the raw extra)"
        ) from e
    tag = f"v{md.version('rawpy')}"
    base = f"{GH_RAW}/{RAWPY_REPO}/{tag}"
    gitmodules = fetch(f"{base}/.gitmodules").decode()
    urls = dict(
        zip(
            re.findall(r"path\s*=\s*(\S+)", gitmodules),
            re.findall(r"url\s*=\s*(\S+)", gitmodules),
            strict=True,
        )
    )
    api = f"{GH_API}/repos/{RAWPY_REPO}/contents/external?ref={tag}"
    shas = {e["path"]: e["sha"] for e in json.loads(fetch(api))}
    flags = getattr(rawpy, "flags", None) or {}
    wanted = {
        "external/LibRaw": "LGPL-2.1-only OR CDDL-1.0",
        "external/LibRaw-cmake": "BSD-3-Clause",
    }
    if flags.get("DEMOSAIC_PACK_GPL2"):
        wanted["external/LibRaw-demosaic-pack-GPL2"] = "GPL-2.0-or-later"
    if flags.get("DEMOSAIC_PACK_GPL3"):
        wanted["external/LibRaw-demosaic-pack-GPL3"] = "GPL-3.0-or-later"
    build_dir = f"rawpy-{tag}-build"
    script = "build-macos.sh" if sys.platform == "darwin" else "build-linux.sh"
    plan = [
        {
            "component": f"rawpy build: {rel}",
            "version": tag,
            "license": "MIT",
            "url": f"{base}/{rel}",
            "kind": "file",
            "dest": f"{build_dir}/{rel}",
        }
        for rel in ("setup.py", ".gitmodules", f".github/scripts/{script}")
    ]
    for path, lic in wanted.items():
        if path not in urls or path not in shas:
            raise SourceFetchError(f"rawpy {tag} does not pin {path}")
        repo = urls[path].removesuffix(".git").rstrip("/")
        sha = shas[path]
        name = path.rsplit("/", 1)[-1]
        plan.append(
            {
                "component": name,
                "version": sha,
                "license": lic,
                "url": f"{repo}/archive/{sha}.tar.gz",
                "kind": "archive",
                "dest": f"{name}-{sha[:12]}.tar.gz",
                "pinned_by": f"rawpy {tag}",
            }
        )
    return plan


# ── download ─────────────────────────────────────────────────────────


def download(plan: list[dict], dest_dir: Path) -> list[dict]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = dest_dir.resolve()
    # The ``dest`` of a patch is a string literal read out of the
    # upstream recipe; keep every write inside the output folder — and
    # refuse the whole plan before the first byte is fetched.
    dests = [(dest_dir / item["dest"]).resolve() for item in plan]
    for item, dest in zip(plan, dests, strict=True):
        if dest == root or not dest.is_relative_to(root):
            raise SourceFetchError(f"refusing {item['dest']!r}: outside {dest_dir}")
    done = []
    for item, dest in zip(plan, dests, strict=True):
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"fetching {item['component']} {item['version']} from {item['url']}")
        data = fetch(item["url"])
        dest.write_bytes(data)
        if item["kind"] == "archive" and not tarfile.is_tarfile(dest):
            raise SourceFetchError(f"{item['url']} did not return a source archive")
        done.append({**item, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    (dest_dir / "SOURCES.json").write_text(json.dumps(done, indent=2) + "\n")
    return done


def fetch_all(dest_dir: Path) -> list[dict]:
    plan = pillow_heif_plan() + rawpy_plan()
    if not plan:
        raise SourceFetchError("nothing to fetch: the source plan is empty")
    return download(plan, dest_dir)
