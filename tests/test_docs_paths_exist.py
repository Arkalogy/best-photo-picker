"""Every repo file the docs point at is a tracked file.

The contributor guide once told people to load legacy JS through a test
helper that had never been in the repo, and the project notes pointed at
a deleted migration doc and a line number that had drifted onto other
code. Docs rot silently; this makes a dead pointer fail the suite. A
``path:line`` pointer fails too: nothing keeps a line number true, so
docs name the symbol instead.

Pointers resolve against ``git ls-files``, not the working tree, so a
developer's stray local file can't make a dead pointer pass here and
fail at the release gate. The gitignored ``CLAUDE.md`` is scanned only
where it exists (its id says ``local``); it drives every session, and a
stale pointer there is exactly what the owner wants to see go red.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# `bpp/web/foo.py`, `docs/API.md`, `_harness.mjs`, `demo.gif`, `x.sh:12`,
# and markdown links `[text](../docs/foo.md#anchor)`. Source, doc, config
# and asset extensions only: `np.stack`, `body.error` and `huggingface.co`
# are prose, not pointers, and a link to `../../security` is a GitHub tab.
_EXT = "py|mjs|js|cjs|ts|md|html|css|yml|yaml|toml|json|jsonl|sh|txt|cfg|ini|rs|gif|png|jpg|svg"
_PATH = r"[A-Za-z0-9_.][A-Za-z0-9_./-]*/"
_NAME = rf"[A-Za-z0-9_.-]+\.(?:{_EXT})"
_CODE_RE = re.compile(rf"`(?P<path>{_PATH})?(?P<name>{_NAME})(?::(?P<line>\d+))?`")
_LINK_RE = re.compile(rf"\]\((?P<path>{_PATH})?(?P<name>{_NAME})(?:#[^)]*)?\)")


def _git_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("needs a git checkout", allow_module_level=True)
    return out.split("\n")


TRACKED = {p for p in _git_files() if p}
TRACKED_NAMES = Counter(Path(p).name for p in TRACKED)


LOCAL_NOTES = "CLAUDE.md (local)"


def _docs() -> list[str]:
    docs = [p for p in sorted(TRACKED) if p.endswith(".md")]
    if (ROOT / "CLAUDE.md").exists():
        docs.append(LOCAL_NOTES)
    return docs


def _looks_like_host(path: str) -> bool:
    first = path.split("/", 1)[0]
    return "." in first and not first.startswith(".")


def _from_doc(doc: Path, target: str) -> str:
    return posixpath.normpath((doc.parent.relative_to(ROOT) / target).as_posix())


def _code_pointer_resolves(doc: Path, path: str | None, name: str) -> bool:
    if not path:
        # A bare name must be unambiguous: a root file, or unique in the repo.
        return name in TRACKED or TRACKED_NAMES[name] == 1
    # From the repo root, from the doc's own folder, or (a pointer like
    # `images/marker-icon.png` in a vendor note) the tail of a tracked path.
    if path + name in TRACKED or _from_doc(doc, path + name) in TRACKED:
        return True
    return any(p.endswith("/" + path + name) for p in TRACKED)


def _link_resolves(doc: Path, target: str) -> bool:
    # Markdown links are relative to the doc, as GitHub renders them.
    return _from_doc(doc, target) in TRACKED or target in TRACKED


def _problems(doc: Path) -> list[str]:
    text = doc.read_text(encoding="utf-8")
    out: list[str] = []
    for m in _CODE_RE.finditer(text):
        path, name, line = m.group("path"), m.group("name"), m.group("line")
        if path and _looks_like_host(path):
            continue  # a URL fragment
        if not _code_pointer_resolves(doc, path, name):
            why = (
                "names several tracked files; give the path"
                if TRACKED_NAMES[name] > 1
                else "not a tracked file"
            )
            out.append(f"{m.group(0)}: {why}")
        elif line:
            out.append(f"{m.group(0)}: a line number can't stay true; name the symbol")
    for m in _LINK_RE.finditer(text):
        target = (m.group("path") or "") + m.group("name")
        if not _link_resolves(doc, target):
            out.append(f"link {target}: not a tracked file")
    return out


@pytest.mark.parametrize("label", _docs())
def test_every_doc_pointer_resolves(label: str) -> None:
    doc = ROOT / ("CLAUDE.md" if label == LOCAL_NOTES else label)
    problems = _problems(doc)
    assert not problems, f"{label}:\n  " + "\n  ".join(problems)
