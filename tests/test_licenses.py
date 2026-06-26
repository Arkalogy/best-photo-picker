"""License regression guard.

Pins the set of copyleft-licensed packages in the dependency tree so
a future transitive-dep upgrade can't silently introduce a new copyleft
obligation without the maintainer noticing.

Strategy: read the PyPI `Classifier: License :: ...` metadata field —
not the raw `License:` text, which is unreliable (many packages embed
the full text of multiple third-party licenses, e.g., numpy embeds GCC
runtime GPL headers). Classifiers are curated by the package author and
are the canonical PyPI source for license identity.

KNOWN_COPYLEFT lists every package carrying a copyleft classifier that
is ALLOWED because it's gated behind an opt-in extra. Any new copyleft
package that appears outside this list fails the test.
"""

from __future__ import annotations

import importlib.metadata as _meta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Copyleft classifier fragments — any classifier substring that indicates
# a strong or weak copyleft obligation.
# ---------------------------------------------------------------------------

_STRONG_COPYLEFT_FRAGMENTS: tuple[str, ...] = (
    # GPL family
    "GNU General Public License",
    "GNU Affero General Public License",
    # EUPL / CDDL (rarely seen in Python but flag them)
    "European Union Public Licence",
    "Common Development and Distribution License",
)

_WEAK_COPYLEFT_FRAGMENTS: tuple[str, ...] = (
    # LGPL — file-level or library copyleft
    "GNU Lesser General Public License",
    # MPL — file-level copyleft (allowable for distribution)
    "Mozilla Public License",
)

# ---------------------------------------------------------------------------
# Known copyleft packages — allowed because they're opt-in extras.
# Format: package_name_lower -> (display, extra, reason)
# ---------------------------------------------------------------------------

KNOWN_COPYLEFT: dict[str, tuple[str, str, str]] = {
    # bppicker[heic]: HEIC/HEIF photo support
    "pillow_heif": (
        "pillow-heif",
        "heic",
        "LGPL-3.0 per upstream; only installed with opt-in [heic] extra",
    ),
    "pillow-heif": (
        "pillow-heif",
        "heic",
        "LGPL-3.0 per upstream; only installed with opt-in [heic] extra",
    ),
    # bppicker[faces]: dlib links OpenBLAS/LAPACK (LGPL transitive via libopenblas)
    "easydict": (
        "easydict",
        "faces",
        "LGPL-3.0; transitive via insightface, faces extra only",
    ),
    # bppicker[nudity]: NudeNet is GPL — users accept by installing the extra
    "nudenet": (
        "nudenet",
        "nudity",
        "GPL-3.0; opt-in [nudity] extra; NOTICE.txt documents the obligation",
    ),
    # bppicker[raw]: rawpy links LibRaw (LGPL-2.1)
    "rawpy": (
        "rawpy",
        "raw",
        "LGPL-2.1 (links LibRaw); opt-in [raw] extra",
    ),
    # transitive dep of jaraco.text → setuptools on Python 3.11.15+
    # (Linux/CI venv). autocommand itself is a pure CLI framework; the
    # LGPL-3.0 obligation is satisfied by dynamic linking (no modification
    # of the library required for bpp). Not a direct dep; added to
    # KNOWN_COPYLEFT to silence the sign-off requirement.
    "autocommand": (
        "autocommand",
        "setuptools (transitive)",
        "LGPL-3.0; transitive via jaraco.text → setuptools; dynamic-link use only",
    ),
    # Desktop-app build toolchain (CI-only; never a runtime dep, never in
    # pyproject deps). PyInstaller is GPL-2.0-or-later WITH
    # Bootloader-exception (SPDX-recognized): the exception grants
    # "unlimited permission to link or embed compiled bootloader and
    # related files into combinations with other programs, and to
    # distribute those combinations without any restriction". The shipped
    # .app sidecar embeds only exception-covered bootloader files +
    # Apache-2.0 runtime hooks — no bare-GPL bytes in the artifact.
    # Standing constraints (NOTICE.txt § Desktop app build toolchain):
    # never modify/fork the bootloader; never vendor PyInstaller into the
    # repo or releases. Decision: pm.md 2026-06-12, option 1.
    "pyinstaller": (
        "pyinstaller",
        "desktop build (CI-only)",
        "GPL-2.0-or-later WITH Bootloader-exception; only the "
        "exception-covered bootloader + Apache-2.0 rthooks are embedded "
        "in the shipped sidecar; tool itself never distributed",
    ),
    # Companion hooks package: its standard hooks are GPL-2.0-or-later but
    # run at BUILD time only (they tell the packager what to bundle and are
    # not copied into the output); the hooks that ARE embedded
    # (_pyinstaller_hooks_contrib/rthooks) are Apache-2.0 — its LICENSE
    # file draws exactly this distinction.
    "pyinstaller_hooks_contrib": (
        "pyinstaller-hooks-contrib",
        "desktop build (CI-only)",
        "Standard hooks GPL-2.0-or-later but build-time only (never "
        "embedded); embedded runtime hooks are Apache-2.0",
    ),
    # docutils ships under a tri-license: Public Domain / BSD-2-Clause /
    # GPL-3.0+ — recipients pick which terms apply to their use. Per the
    # docutils COPYING file: "all the materials in this distribution
    # ... are placed in the public domain except for [BSD-licensed and
    # GPL-licensed exceptions]." The default + BSD branches are
    # permissive; the GPL appearing in classifiers is offered, not
    # imposed. Transitive via Sphinx → onnxruntime build chain on Linux
    # (not used at runtime by bpp). Documenting here so the strong-copyleft
    # guard doesn't trip on the GPL classifier.
    "docutils": (
        "docutils",
        "transitive (Sphinx / onnxruntime build chain)",
        "Tri-licensed Public Domain / BSD-2-Clause / GPL-3.0+; user "
        "selects terms. We use under BSD/Public Domain. Not in any "
        "runtime path; only present on Linux as a build-time transitive.",
    ),
}

# MPL-2.0 is file-level copyleft: modifications to the MPL'd files must
# be shared, but it does not spread to caller code. We allow it everywhere.
_MPL_ALLOWED = True


def _classifiers_for(dist: _meta.Distribution) -> list[str]:
    """Return all `Classifier: License :: ...` values for a distribution."""
    return [c for c in (dist.metadata.get_all("Classifier") or []) if c.startswith("License ::")]


def _is_strong_copyleft(classifiers: list[str]) -> bool:
    combined = " ".join(classifiers)
    return any(frag in combined for frag in _STRONG_COPYLEFT_FRAGMENTS)


def _is_weak_copyleft(classifiers: list[str]) -> bool:
    combined = " ".join(classifiers)
    return any(frag in combined for frag in _WEAK_COPYLEFT_FRAGMENTS)


def _is_mpl(classifiers: list[str]) -> bool:
    return any("Mozilla Public License" in c for c in classifiers)


def _norm_key(name: str) -> str:
    return name.lower().replace("-", "_")


def _known_key(name: str) -> bool:
    k = _norm_key(name)
    return k in {_norm_key(kk) for kk in KNOWN_COPYLEFT}


def _get_all_dists() -> list[tuple[str, str, list[str]]]:
    """Return [(name, version, license_classifiers), ...] sorted by name."""
    result = []
    for dist in _meta.distributions():
        name = dist.metadata.get("Name") or ""
        version = dist.metadata.get("Version") or ""
        classifiers = _classifiers_for(dist)
        result.append((name, version, classifiers))
    return sorted(result, key=lambda t: t[0].lower())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_unexpected_strong_copyleft_in_dep_tree():
    """No GPL/AGPL/EUPL package may appear in the dep tree unless it's in
    KNOWN_COPYLEFT (gated behind an opt-in extra).

    Uses PyPI `Classifier: License ::` metadata rather than the raw
    License text, which is unreliable (e.g., numpy embeds GCC runtime GPL
    headers in its bundled-license block even though numpy itself is BSD).
    """
    violations: list[str] = []

    for name, version, classifiers in _get_all_dists():
        if not classifiers:
            # No License classifier — can't determine; skip to avoid noise.
            continue
        if not _is_strong_copyleft(classifiers):
            continue
        if _known_key(name):
            continue
        classifier_str = "; ".join(classifiers)
        violations.append(f"{name} {version}: {classifier_str!r}")

    assert not violations, (
        "New GPL/AGPL package(s) appeared in the dep tree — review before "
        "shipping. Is it gated behind an opt-in extra? If yes, add to "
        "KNOWN_COPYLEFT with justification. If no, pin to a permissive "
        "version:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_no_unexpected_lgpl_in_dep_tree():
    """LGPL packages must either be in KNOWN_COPYLEFT or be MPL-only.

    LGPL does not spread to caller code (dynamic linking is fine), but
    for a packaged Python app it still deserves explicit sign-off.
    MPL-2.0 (Mozilla) is file-level copyleft and is globally allowed.
    """
    violations: list[str] = []

    for name, version, classifiers in _get_all_dists():
        if not classifiers:
            continue
        if not _is_weak_copyleft(classifiers):
            continue
        if _is_mpl(classifiers):
            # MPL-2.0 file-level copyleft — allowed everywhere.
            continue
        if _known_key(name):
            continue
        classifier_str = "; ".join(classifiers)
        violations.append(f"{name} {version}: {classifier_str!r}")

    assert not violations, (
        "New LGPL package(s) not in KNOWN_COPYLEFT appeared in the dep tree.\n"
        "LGPL is acceptable (dynamic linking is fine) but each new entry\n"
        "needs a sign-off comment in KNOWN_COPYLEFT:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_known_copyleft_packages_are_documented():
    """Every KNOWN_COPYLEFT entry must have a non-empty justification."""
    for key, (display, extra, reason) in KNOWN_COPYLEFT.items():
        assert reason, f"KNOWN_COPYLEFT[{key!r}] has no justification"
        assert extra, f"KNOWN_COPYLEFT[{key!r}] must name the optional extra"
        assert display, f"KNOWN_COPYLEFT[{key!r}] must have a display name"


@pytest.mark.parametrize(
    "extra_pkg,extra_name",
    [
        ("pillow-heif", "heic"),
        ("nudenet", "nudity"),
    ],
)
def test_known_copyleft_extras_not_in_base_dependencies(extra_pkg, extra_name):
    """Copyleft packages tied to opt-in extras must NOT be in pyproject's
    base `dependencies` list — the license gate depends on opt-in installs.
    """
    import tomllib

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    base_deps = [
        d.split(">=")[0].split("[")[0].strip().lower().replace("-", "_")
        for d in pyproject["project"].get("dependencies", [])
    ]
    pkg_key = extra_pkg.replace("-", "_").lower()
    assert pkg_key not in base_deps, (
        f"{extra_pkg!r} is copyleft and must NOT be in pyproject base "
        f"dependencies — it's gated via the [{extra_name}] extra."
    )
