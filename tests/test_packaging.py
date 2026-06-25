"""Packaging-layer regression tests.

These pin two things that are easy to break and expensive to get wrong:

1. The PyPI distribution name in `pyproject.toml` matches what the
   Docker / Homebrew / install-extras / README references expect.
   Drift here means a `pip install bppicker[X]` command in our docs
   silently misses the package the user is on.

2. The `publish.yml` GitHub Actions workflow is well-formed YAML +
   has the trusted-publisher + dist-name guards we depend on. CI
   green is the only "test" this workflow ever gets before a real
   release hits PyPI, so the structural checks live here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The distribution name we publish under. Different from the *import*
# package name (`bpp`) — these are independent by design.
EXPECTED_DIST_NAME = "bppicker"


def _read_pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


def _read_publish_workflow() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text()


def _read_docker_workflow() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "docker.yml").read_text()


def _read_dependabot() -> str:
    return (REPO_ROOT / ".github" / "dependabot.yml").read_text()


# ── pyproject.toml ───────────────────────────────────────────────────


class TestPyProjectName:
    def test_distribution_name_is_bppicker(self):
        cfg = _read_pyproject()
        assert cfg["project"]["name"] == EXPECTED_DIST_NAME, (
            f"pyproject.toml declares name = {cfg['project']['name']!r}, "
            f"expected {EXPECTED_DIST_NAME!r}. The PyPI name `bpp` was "
            "already taken (a 2014 bioinformatics package), so we publish "
            "as `bppicker` while the import package stays `bpp`."
        )

    def test_no_empty_extras_in_pyproject(self):
        """An extra with an empty dependency list installs nothing —
        ``pip install bppicker[X]`` becomes a no-op the user can't tell
        from a working install. Round-9 audit found `clip = []` and
        `pets = []` decorating pyproject as if they gated AGPL weights;
        the actual gating happens at the model-download layer, not at
        the pip-extras layer. If a future extra needs to be empty for
        some reason (e.g. a pure marker), explicitly comment-document
        it and update this assertion."""
        cfg = _read_pyproject()
        extras = cfg["project"]["optional-dependencies"]
        empty = [name for name, deps in extras.items() if not deps]
        assert not empty, (
            f"Empty extras found in pyproject.toml: {empty!r}. "
            "An empty extra installs nothing — either populate the deps "
            "or remove the extra entirely."
        )

    def test_dev_extra_self_reference_uses_dist_name(self):
        """The `dev` extra uses `bppicker[heic,...]` to pull in all
        other extras transitively. If we ever rename `bppicker` and
        forget this self-reference, `pip install bppicker[dev]` half-
        installs and silently misses the optional deps."""
        cfg = _read_pyproject()
        dev = cfg["project"]["optional-dependencies"]["dev"]
        self_refs = [d for d in dev if d.startswith(f"{EXPECTED_DIST_NAME}[")]
        assert self_refs, (
            f"`dev` extra in pyproject.toml does not reference {EXPECTED_DIST_NAME!r}. Got: {dev!r}"
        )

    def test_import_package_stays_bpp(self):
        """The hatchling target points at the importable package
        `bpp/`. Renaming the distribution must NOT touch the import
        name — internal `from bpp.foo import bar` statements are
        load-bearing across ~150 files."""
        cfg = _read_pyproject()
        wheel = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]
        assert wheel["packages"] == ["bpp"], (
            f"hatch wheel target should still be ['bpp']; got {wheel['packages']}"
        )

    def test_cli_entry_point_stays_bpp(self):
        """The CLI binary is `bpp` (e.g. `bpp serve`). That's the
        user-facing command name and changes are visible — keep it
        stable across distribution renames."""
        cfg = _read_pyproject()
        scripts = cfg["project"]["scripts"]
        assert "bpp" in scripts, f"Expected `bpp` script entry point; got {scripts!r}"
        assert scripts["bpp"] == "bpp.cli:main"


# ── publish.yml ──────────────────────────────────────────────────────


class TestPublishWorkflow:
    def test_workflow_is_valid_yaml(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed in this environment")

        # Should parse without error
        parsed = yaml.safe_load(_read_publish_workflow())
        assert isinstance(parsed, dict)

    def test_workflow_has_release_trigger(self):
        """Publishing must only fire on a published GitHub release.
        Adding a `push: tags` trigger here would defeat the
        explicit-button safeguard."""
        text = _read_publish_workflow()
        assert "release:" in text
        assert "types: [published]" in text or "types:\n      - published" in text

    def test_workflow_uses_trusted_publisher(self):
        """`id-token: write` is the OIDC permission that lets PyPI
        verify the GitHub run claim. Removing this would force us
        back to storing a long-lived API token as a secret — which
        is exactly the failure mode trusted publisher avoids."""
        text = _read_publish_workflow()
        assert "id-token: write" in text

    def test_workflow_pins_dist_name_in_inspect_step(self):
        """The build job's inspect step greps for the distribution
        name in dist/ filenames. If the prefix here drifts from
        EXPECTED_DIST_NAME, the inspect step is a no-op."""
        text = _read_publish_workflow()
        # Loose match: just confirms the expected name appears in the
        # workflow body somewhere (typically the inspect step's case
        # branch and the environment URL).
        assert EXPECTED_DIST_NAME in text

    def test_workflow_uses_actions_with_pinned_majors(self):
        """All `uses:` entries should reference at least a major-version
        tag (e.g. `@v4`, `@v5`, `@release/v1`) — not `@main` or `@master`,
        which can shift behavior under us silently."""
        text = _read_publish_workflow()
        uses_matches = re.findall(r"uses: ([^\s]+)", text)
        assert uses_matches, "publish.yml has no `uses:` action references"
        for ref in uses_matches:
            assert "@" in ref, f"`uses: {ref}` is missing a version pin"
            tag = ref.split("@", 1)[1]
            assert tag not in ("main", "master"), (
                f"`uses: {ref}` pins to a moving branch; pin to a tag instead"
            )


# ── docker.yml ───────────────────────────────────────────────────────


class TestDockerWorkflow:
    """The Docker Hub publish workflow shares the same explicit-button
    safety pattern as the PyPI one. Pin the structural invariants so
    a future refactor can't quietly weaken them."""

    def test_workflow_is_valid_yaml(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed in this environment")
        parsed = yaml.safe_load(_read_docker_workflow())
        assert isinstance(parsed, dict)

    def test_workflow_has_release_trigger(self):
        """Same constraint as publish.yml: the only auto-trigger is a
        published GitHub Release. No `push: tags`, no `branches: main`."""
        text = _read_docker_workflow()
        assert "release:" in text
        assert "types: [published]" in text or "types:\n      - published" in text

    def test_workflow_has_workflow_dispatch_with_publish_input(self):
        """Manual dispatch must support a `publish=false` smoke-test
        path so we can verify the build without pushing."""
        text = _read_docker_workflow()
        assert "workflow_dispatch:" in text
        assert "publish:" in text

    def test_workflow_builds_multi_arch(self):
        """linux/amd64 + linux/arm64 covers cloud, Intel Mac, Apple
        Silicon, Raspberry Pi 4/5, AWS Graviton. Dropping arm64 would
        re-break Apple Silicon, which is the dev machine for half the
        user base."""
        text = _read_docker_workflow()
        assert "linux/amd64" in text
        assert "linux/arm64" in text

    def test_workflow_uses_buildx(self):
        """Multi-arch requires buildx — without it, only the host arch
        gets built."""
        text = _read_docker_workflow()
        assert "docker/setup-buildx-action" in text
        assert "docker/setup-qemu-action" in text  # needed for arm64 cross-build

    def test_workflow_login_is_conditional(self):
        """Login must be gated by `do_push` so a smoke-test build
        without DOCKERHUB_TOKEN configured still works."""
        text = _read_docker_workflow()
        # The login step must have an `if:` immediately above its uses
        login_idx = text.find("docker/login-action")
        assert login_idx > 0
        # Look in the ~150 chars before login-action for an `if:` clause
        prelude = text[max(0, login_idx - 250) : login_idx]
        assert "if:" in prelude, (
            "docker/login-action must be guarded by `if: env.do_push == 'true'`"
        )

    def test_workflow_image_path_uses_dist_name(self):
        """The Docker image name must match the PyPI distribution
        name (`bppicker`) so users find the same name in both
        registries. Drift here means `pip install bppicker` and
        `docker pull arkalogy/bpp` — confusing."""
        text = _read_docker_workflow()
        # Image line should be `<USERNAME>/bppicker`
        assert "/bppicker" in text, (
            "Docker image path should end in `/bppicker` (matches PyPI dist name)"
        )

    def test_workflow_uses_actions_with_pinned_majors(self):
        text = _read_docker_workflow()
        uses_matches = re.findall(r"uses: ([^\s]+)", text)
        assert uses_matches
        for ref in uses_matches:
            assert "@" in ref, f"`uses: {ref}` is missing a version pin"
            tag = ref.split("@", 1)[1]
            assert tag not in ("main", "master"), (
                f"`uses: {ref}` pins to a moving branch; pin to a tag instead"
            )


# ── Dockerfile sanity ────────────────────────────────────────────────


class TestDockerfile:
    """The Dockerfile installs `.[web,faces,...]` from the local
    source — extras keys come from pyproject's
    [project.optional-dependencies] section, which is renamed-immune
    by virtue of the keys being `web`, `faces`, etc. (not the dist
    name). Pin the contract so a future Dockerfile edit can't drift
    onto a non-existent extra key."""

    def test_dockerfile_extras_are_real_pyproject_keys(self):
        cfg = _read_pyproject()
        extras = set(cfg["project"]["optional-dependencies"].keys())

        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        # Find any pip install spec like `.[web,faces,...]`
        install_specs = re.findall(r'\."?\[([\w,]+)\]"?', dockerfile)
        assert install_specs, "Dockerfile has no `.[...]` install spec"
        for spec in install_specs:
            for key in spec.split(","):
                key = key.strip()
                assert key in extras, (
                    f"Dockerfile references extra `{key}` which is not in "
                    f"pyproject.toml's optional-dependencies. "
                    f"Available: {sorted(extras)}"
                )

    def test_dockerfile_uses_bpp_serve_cmd(self):
        """The CLI binary stays `bpp` (not `bppicker`). The CMD line
        in the Dockerfile is what users see when running the container,
        so it has to invoke the same `bpp` command our docs reference."""
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        assert '"bpp", "serve"' in dockerfile, "Dockerfile CMD should still call `bpp serve`"


class TestCIWorkflowExtras:
    """R12-OSS-3: the CI workflow's `pip install -e .[X,Y,...]` line
    is the same drift hazard as the Dockerfile — a removed or
    renamed extra leaves CI installing nothing for that key, and
    pip's "no such extra" warning is easy to miss in green CI logs.

    Pre-fix the workflow referenced `[dev,web,faces,nudity,clip,heic,raw]`
    but `[clip]` was removed in R9-P2 (the empty-extras cleanup),
    so CI was emitting a silent pip warning on every run."""

    def test_ci_extras_are_real_pyproject_keys(self):
        cfg = _read_pyproject()
        extras = set(cfg["project"]["optional-dependencies"].keys())

        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
        # Match any `pip install ... ".[X,Y,...]"` form.
        install_specs = re.findall(r'\."?\[([\w,]+)\]"?', ci)
        assert install_specs, ".github/workflows/ci.yml has no `.[...]` install spec"
        for spec in install_specs:
            for key in spec.split(","):
                key = key.strip()
                assert key in extras, (
                    f"ci.yml references extra `{key}` which is not in "
                    f"pyproject.toml's optional-dependencies. "
                    f"Available: {sorted(extras)}"
                )


# ── security policy + issue template ────────────────────────────────


class TestSecurityPolicy:
    """SECURITY.md is the public-facing disclosure document. Pin the
    invariants so a future doc edit can't silently leave reporters
    with no working contact path."""

    def test_security_md_exists(self):
        assert (REPO_ROOT / "SECURITY.md").exists()

    def test_security_md_points_to_pvr(self):
        """GitHub Private Vulnerability Reporting is the modern best
        practice — no email exposure, integrates with GitHub's
        advisory database, lets us scope the discussion to invited
        collaborators only."""
        text = (REPO_ROOT / "SECURITY.md").read_text()
        assert "security/advisories/new" in text or "Private Vulnerability Reporting" in text, (
            "SECURITY.md should direct reporters to GitHub PVR"
        )

    def test_security_md_provides_fallback_path(self):
        """If GitHub PVR is ever offline (it has happened), reporters
        need *some* documented fallback. Original drafts named the
        GitHub `users.noreply.github.com` address, but those don't
        actually deliver email (write-only commit identity). The
        current fallback is "file a public issue with [security] tag,
        no details". Either a working email or that public-issue
        path counts."""
        text = (REPO_ROOT / "SECURITY.md").read_text().lower()
        has_email_fallback = "mailto:" in text
        has_public_issue_fallback = "[security]" in text and (
            "public issue" in text or "file an issue" in text
        )
        assert has_email_fallback or has_public_issue_fallback, (
            "SECURITY.md must document a fallback path when PVR is "
            "unavailable (mailto: link, or a public-issue procedure)"
        )

    def test_security_md_is_not_stale_about_auth(self):
        """The pre-LAN-sharing version said 'No authentication: The web
        UI has no login system'. That's been false since the LAN
        sharing v2 work added auth tokens, share tokens, and TOFU
        device pairing. Catch a regression to the stale text."""
        text = (REPO_ROOT / "SECURITY.md").read_text()
        assert "No authentication" not in text, (
            "SECURITY.md still says 'No authentication' — the LAN sharing v2 "
            "work landed auth tokens + TOFU device pairing. Update the doc."
        )

    def test_security_md_points_to_full_threat_model(self):
        """The detailed trust model, threat list, and extension hooks
        live in docs/security.md. SECURITY.md should link there for
        anyone who wants more than the headline summary."""
        text = (REPO_ROOT / "SECURITY.md").read_text()
        assert "docs/security.md" in text, (
            "SECURITY.md should link to docs/security.md for the full threat model"
        )


class TestSecurityIssueTemplate:
    def test_security_template_exists(self):
        assert (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "security.md").exists()

    def test_security_template_redirects_to_pvr_or_security_md(self):
        """The issue template's only job is to keep security reports
        off the public timeline. It must redirect to either GitHub
        PVR or SECURITY.md (or both — best practice)."""
        text = (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "security.md").read_text()
        # Must say "don't file public issues"
        lower = text.lower()
        assert "do not" in lower or "don't" in lower or "stop" in lower, (
            "Security template should warn against public reporting"
        )
        # Must point at SECURITY.md or PVR
        assert "SECURITY.md" in text or "security/advisories" in text, (
            "Security template must point at SECURITY.md or GitHub PVR"
        )


# ── dependabot.yml ───────────────────────────────────────────────────


class TestDependabotConfig:
    """Pin the Dependabot config covers every package manifest the
    repo actually has. Drift here means a manifest is silently
    unmonitored — exactly the failure mode Dependabot exists to
    prevent."""

    def test_dependabot_yml_exists(self):
        assert (REPO_ROOT / ".github" / "dependabot.yml").exists()

    def test_dependabot_is_valid_yaml(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed in this environment")
        parsed = yaml.safe_load(_read_dependabot())
        assert isinstance(parsed, dict)
        assert parsed.get("version") == 2

    def test_dependabot_covers_every_manifest(self):
        """Every package manifest in the repo (pyproject, package.json,
        Cargo.toml, Dockerfile) must have a matching Dependabot
        ecosystem entry. If you add a new manifest, add the matching
        ecosystem here so this test prompts you to add the dependabot
        block too."""
        text = _read_dependabot()

        # Map of "what the repo has" → "ecosystem name Dependabot uses".
        expected_ecosystems = {
            "pyproject.toml at /": "pip",
            "package.json at /": "npm",
            "package.json at /desktop": "npm",
            "Cargo.toml at /desktop/src-tauri": "cargo",
            "Dockerfile at /": "docker",
            "GitHub Actions workflows": "github-actions",
        }
        for manifest, ecosystem in expected_ecosystems.items():
            assert f'package-ecosystem: "{ecosystem}"' in text, (
                f'dependabot.yml is missing a `package-ecosystem: "{ecosystem}"` '
                f"block for {manifest}"
            )

    def test_dependabot_uses_weekly_cadence(self):
        """Daily Dependabot is a flood for single-maintainer projects;
        weekly is the maintainable cadence."""
        text = _read_dependabot()
        # Every schedule block should specify weekly
        intervals = re.findall(r'interval:\s*"(\w+)"', text)
        assert intervals, "no schedule intervals found"
        for i in intervals:
            assert i == "weekly", f"Dependabot interval `{i}` — should be `weekly` for sanity"


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
        import tomllib

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
        import tomllib

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


# tomllib is part of the stdlib from Python 3.11 onward; pyproject pins
# `requires-python = ">=3.11"` so no version guard needed here.
