"""Packaging-layer regression tests — part 2 of 2: TestDocsConsistency."""

from __future__ import annotations

import re
import tomllib

from tests._packaging_shared import (
    REPO_ROOT,
)

# ── docs sanity ──────────────────────────────────────────────────────


class TestDocsConsistency:
    """If docs reference `pip install <name>` or `<name>[extra]`, the
    name has to match `EXPECTED_DIST_NAME`. Easy thing to miss when
    renaming."""

    def test_readme_uses_bppicker_for_extras(self):
        readme = (REPO_ROOT / "README.md").read_text()
        # Any literal extras references must use the new name
        old_refs = re.findall(r"`bpp\[[^\]]+\]`", readme)
        assert not old_refs, f"README.md still references the old `bpp[extra]` form: {old_refs}"

    def test_notice_uses_bppicker(self):
        notice = (REPO_ROOT / "NOTICE.txt").read_text()
        old_refs = re.findall(r"`bpp\[[^\]]+\]`", notice)
        assert not old_refs, f"NOTICE.txt still references the old `bpp[extra]` form: {old_refs}"
        assert "pip install bpp\n" not in notice, (
            "NOTICE.txt has a bare `pip install bpp` — should be `bppicker`"
        )

    def test_notice_extras_match_pyproject_extras(self):
        """NOTICE.txt names extras that pyproject.toml actually
        defines. Catches a `nsfw` → `nudity` style drift where a
        user following the install path documented in NOTICE gets
        pip's "no such extra" warning because the extra name has
        been renamed in pyproject without mirroring here."""

        notice = (REPO_ROOT / "NOTICE.txt").read_text()
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        defined = set(pyproject["project"].get("optional-dependencies", {}).keys())

        # Every `bppicker[<name>]` in NOTICE must be a defined extra
        referenced = set(re.findall(r"bppicker\[([^\]]+)\]", notice))
        # Compound extras like bppicker[heic,web,faces] are commas — split
        atomic_refs = {part.strip() for ref in referenced for part in ref.split(",")}
        missing = atomic_refs - defined
        assert not missing, (
            f"NOTICE.txt references extras not in pyproject.toml: {missing}. "
            f"Defined extras: {sorted(defined)}"
        )

    def test_no_stale_bpp_extras_references_in_source(self):
        """The PyPI distribution is `bppicker`, not `bpp` (the slot
        was taken by an unrelated 2014 package). Any user-facing
        install hint that says `pip install bpp[X]` resolves to the
        wrong package on PyPI.

        This guard sweeps the source tree (excluding tests + docs
        that already have their own coverage) for `bpp[...]`-style
        install hints and fails the build on any recurrence."""
        # Source areas we expect to be stale-free
        source_dirs = [
            REPO_ROOT / "bpp",
            REPO_ROOT / "bpp" / "web" / "static" / "js",
            REPO_ROOT / "bpp" / "web" / "templates",
            REPO_ROOT / "Dockerfile",
            REPO_ROOT / ".github" / "workflows",
        ]
        # Test fixtures may legitimately use `bpp[X]` strings as
        # negative-test inputs (e.g. asserting we DON'T render them).
        # The README/NOTICE checks earlier already cover docs.
        bad_pattern = re.compile(r"\bpip install bpp\[")
        offenders: list[str] = []
        for root in source_dirs:
            if not root.exists():
                continue
            if root.is_file():
                files = [root]
            else:
                files = [
                    p
                    for p in root.rglob("*")
                    if p.is_file()
                    and p.suffix in {".py", ".mjs", ".js", ".html", ".yml", ""}
                    and "node_modules" not in p.parts
                    and "vendor" not in p.parts
                ]
            for p in files:
                try:
                    text = p.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if bad_pattern.search(line):
                        offenders.append(f"{p.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
        assert not offenders, (
            "Found stale `pip install bpp[...]` references in source; "
            "the PyPI dist is `bppicker`. Update each match:\n  " + "\n  ".join(offenders)
        )

    def test_python_classifiers_match_ci_matrix(self):
        """`Programming Language :: Python :: X.Y` classifiers in
        pyproject.toml must match the python-version values in
        .github/workflows/ci.yml. PyPI users searching for a
        specific Python version trust the classifier list — adding
        an untested version is an overclaim.

        Catches drift between classifiers and the CI matrix in
        either direction (CI matrix change without classifier update,
        or vice versa)."""

        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        classifiers = pyproject["project"].get("classifiers", [])

        # Extract version strings from classifier rows like
        # "Programming Language :: Python :: 3.11"
        classifier_versions = {
            m.group(1)
            for c in classifiers
            if (m := re.match(r"Programming Language :: Python :: (\d+\.\d+)$", c))
        }

        # Extract `python-version: "X.Y"` from the CI workflow
        ci_text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
        ci_versions = set(re.findall(r'python-version:\s*"(\d+\.\d+)"', ci_text))

        assert classifier_versions, (
            "pyproject.toml has no `Programming Language :: Python :: X.Y` "
            "classifiers — at least the supported version must be listed."
        )
        assert ci_versions, "ci.yml has no python-version entries to verify against."

        # Classifier set must be a subset of CI set — claiming
        # support for a version CI doesn't run is the overclaim we
        # want to prevent.
        unverified = classifier_versions - ci_versions
        assert not unverified, (
            f"pyproject classifies Python {sorted(unverified)} but CI "
            f"never runs against them. Either expand CI or drop the "
            f"classifier rows. CI runs: {sorted(ci_versions)}; "
            f"classifiers: {sorted(classifier_versions)}."
        )
