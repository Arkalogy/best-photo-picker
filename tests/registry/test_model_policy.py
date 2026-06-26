"""Batch 10 — contributor-policy gates (item 22).

Pins:

* ``MODEL_POLICY.md`` exists and references each load-bearing
  mechanism it claims to govern.
* ``.github/CODEOWNERS`` exists and gates the
  legally-sensitive paths.
* ``CONTRIBUTING.md`` links to ``MODEL_POLICY.md`` in the
  Adding-models section so a contributor lands on the policy
  before they write the PR.
* No binary model weights are tracked by git outside the
  vendor + test fixture allowlist. Catches a future PR that
  accidentally checks in a ``.onnx``, ``.pt``, ``.bin``, etc.

The CODEOWNERS test is intentionally generous on team-name
matching (the Arkalogy team slug can change between repo
moves); what we pin is "this path is in the file" not "this
exact handle owns it."
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


# ── MODEL_POLICY.md surface ──


class TestModelPolicyExists:
    """The file ships, and references every mechanism it claims to
    govern. Each phrase is load-bearing in the policy text — losing
    one means the contributor lands on a doc that no longer matches
    what the code enforces."""

    def test_file_present(self) -> None:
        assert (REPO_ROOT / "MODEL_POLICY.md").exists()

    @pytest.mark.parametrize(
        "phrase",
        [
            # Mentions each gate the policy claims to enforce.
            "download_file",
            "BPP_DISABLE_REMOTE_REGISTRY",
            "Ed25519",
            "dual-signature",
            "CANONICAL_DISCLAIMER",
            "ModelSingleton",
            "SFace",
            # The three load-bearing claims headline.
            "What this policy protects",
            # The CODEOWNERS reference.
            "CODEOWNERS",
        ],
    )
    def test_policy_references_mechanism(self, phrase: str) -> None:
        body = _read("MODEL_POLICY.md")
        assert phrase in body, (
            f"MODEL_POLICY.md no longer references {phrase!r}. Either "
            "the policy text was edited away from the mechanism it "
            "claims to govern, or the mechanism was renamed without "
            "updating the policy."
        )


# ── CODEOWNERS ──


class TestCodeownersGates:
    def test_file_present(self) -> None:
        assert (REPO_ROOT / ".github" / "CODEOWNERS").exists()

    @pytest.mark.parametrize(
        "path_marker",
        [
            "/bpp/registry/",
            "/bpp/utils/download.py",
            "/MODEL_POLICY.md",
            "/.github/CODEOWNERS",
        ],
    )
    def test_legally_sensitive_path_is_gated(self, path_marker: str) -> None:
        body = _read(".github/CODEOWNERS")
        assert path_marker in body, (
            f"CODEOWNERS no longer lists {path_marker!r}. Either the "
            "path was renamed without updating the gate, or someone "
            "removed a load-bearing entry. Re-add with the maintainer "
            "team handle."
        )


# ── CONTRIBUTING.md pointer ──


class TestContributingLinksPolicy:
    def test_contributing_links_to_model_policy(self) -> None:
        body = _read("CONTRIBUTING.md")
        assert "MODEL_POLICY.md" in body, (
            "CONTRIBUTING.md no longer links to MODEL_POLICY.md. A "
            "contributor who lands on CONTRIBUTING first must be "
            "directed to the model policy before they write the PR."
        )


# ── No binary weights in the repo ──


# Extensions banned from the git tree. The first cohort is the
# usual model-weight formats; .npy is added because numpy arrays
# are sometimes saved as "weights" by lazy upstream code.
BANNED_WEIGHT_EXTENSIONS = (
    ".onnx",
    ".pt",
    ".bin",
    ".dat",
    ".task",
    ".safetensors",
    ".npy",
    ".pickle",
    ".pkl",
)

# Paths under which a banned extension is allowed. Vendored JS / CSS
# is the only legitimate ".bin"-adjacent case (font subset, sourcemap
# index), and test-fixture binaries are by definition synthetic.
ALLOWED_BANNED_EXTENSION_PATH_PREFIXES = (
    "bpp/web/static/vendor/",
    "tests/fixtures/",
    "tests-e2e/fixtures/",
)


class TestNoBinaryModelWeights:
    """Catches the canonical Item-22 mistake: a contributor adds a
    `.onnx` or `.pt` to the repo so the model 'just works for the
    grader.' The git ls-files scan is the cheapest possible enforcer."""

    def test_no_banned_extensions_tracked(self) -> None:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        offenders: list[str] = []
        for path in result.stdout.splitlines():
            if not path:
                continue
            if any(path.startswith(prefix) for prefix in ALLOWED_BANNED_EXTENSION_PATH_PREFIXES):
                continue
            for ext in BANNED_WEIGHT_EXTENSIONS:
                if path.endswith(ext):
                    offenders.append(path)
                    break
        assert not offenders, (
            "Binary model weight(s) tracked by git: "
            f"{offenders}. MODEL_POLICY.md forbids weights in the "
            "repo — they live on the user's machine after first-use "
            "download. If this is a vendored asset (e.g. a Leaflet "
            "marker), add its directory prefix to "
            "ALLOWED_BANNED_EXTENSION_PATH_PREFIXES."
        )
