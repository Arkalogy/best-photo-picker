# Model Policy

This document is the contributor policy for adding, modifying, or removing ML models in bppicker. It exists because a well-meaning pull request can introduce real legal exposure — a non-permissive model file checked into git, an unreviewed download URL, a restriction-class downgrade that bypasses the click-through gate. A typical code review can miss all of these.

The rules below apply to any PR that touches the model registry, the download chokepoint, or anything under `bpp/registry/`. Some are checked by tests and CI (listed in [Test gates](#test-gates)); the rest are checked by the maintainer when reviewing the PR. Nothing in GitHub's branch settings blocks a merge on its own — see [CODEOWNERS](#codeowners) for what is and isn't automated.

If a rule below seems inconvenient, that is the rule working as intended.

## What this policy protects

bppicker's legal posture is built on three load-bearing claims:

1. **bppicker does not bundle or redistribute restricted model weights.** The one model file shipped in the repo and the wheel is MediaPipe's BlazeFace short-range face detector (`bpp/scoring/models/blaze_face_short_range.tflite`, Apache 2.0, credited in `NOTICE.txt`), used as a fallback face detector. (The macOS desktop app additionally contains OpenCV's frontal-face Haar cascade, which ships inside the permissively licensed OpenCV wheel.) Every other model is downloaded by the user's local installation on first use from a third-party host (the model's publisher, or a public re-host of its weights — never Arkalogy), subject to the model's upstream terms.
2. **Changes to the bundled registry baseline never silently add, remove, or relax a model's restriction class.** Tightening (making a model less available) is routine. Relaxing a restriction in the bundled baseline needs a written rationale and maintainer review; relaxing one through the remote overlay needs a manifest that meets the signature quorum (see [Signed remote registry overlay](#signed-remote-registry-overlay)).
3. **Restricted-license models are not the default.** SFace is the default face embedder, and a test fails if a restricted entry is ever marked as a default. Any other embedder reaches users only via the opt-in click-through gate, and restricted models are blocked in commercial-use mode unless the user asserts separate rights for that specific model.

Every rule in this document protects one of those three claims.

**Terms used here.** A **restricted** model is a registry entry with `commercial_use_restriction_known=True`: its upstream terms restrict commercial use, so it is never a default and the commercial use-context gate applies to it. A **click-through** entry is one with `requires_explicit_ack=True`: the user must accept its terms before it downloads. Every restricted entry is a click-through entry, but not the other way round — permissive models whose license carries attribution terms (SFace, YuNet, the MediaPipe models and OpenCV's Haar cascade) are click-through without being restricted. Those attribution-only entries are accepted together in one step before the first analysis (or with `bpp model accept-attribution`); restricted entries are never part of that step.

**One source for licenses.** Every model bppicker runs — including the supporting MediaPipe models and the Haar cascade — has a registry entry, and the entry is the only place its license is written. Detectors, embedders, model loaders and download manifests link to an entry by id, and every license label is derived from it; tests fail if a model has no entry, or if a detector, embedder or model description writes its own license.

## Hard rules

### No new model weights in the repo

Apart from the BlazeFace file above, the git tree contains no `.onnx`, `.pt`, `.bin`, `.dat`, `.task`, `.safetensors`, `.npy`, `.pkl`, or `.pickle` files outside of `bpp/web/static/vendor/` (vendored JS / CSS) and test-fixture directories. Two checks cover this: `tests/registry/test_model_policy.py` scans `git ls-files`, and `scripts/check_no_bundled_weights.py` scans the `bpp/` tree and the built wheel in CI (its allowlist holds only the BlazeFace file). Model weights live on the user's machine after first-use download — not in the repo and not in the wheel.

If you need a model file to write a test, add a tiny synthetic fixture under `tests/fixtures/` (allowlisted by the scan) — and never call it a "model weight."

### No unreviewed model URLs

A new `source_url` or `terms_url` in the model registry must:

- Point at an HTTPS URL whose host the maintainer has reviewed for the upstream license and provenance
- Carry the upstream license terms in the same PR (in the registry `license_summary` field, and in `docs/` or `NOTICE.txt` if appropriate)
- Be reviewed and approved by the maintainer before merge (see [CODEOWNERS](#codeowners))

A PR that adds a URL without a license review attached will be closed without merge.

### No new restricted or click-through models without an issue first

This rule covers new restricted entries and new click-through entries alike (see [Terms used here](#what-this-policy-protects)). Adding either to the bundled baseline requires:

1. A GitHub issue describing why the model is being added, the upstream license, the commercial-use restriction, and which acceptance text version users will sign
2. Maintainer approval on the issue before the PR is opened
3. Maintainer approval on the PR itself
4. New tests pinning the entry's policy decisions (`requires_explicit_ack`, `commercial_use_restriction_known`, the `ack_text_kind` discriminator) so a future edit of the registry can't silently flip them — see `tests/registry/test_restricted_entries.py` and `tests/registry/test_buffalo_s_entry.py` for the pattern

PRs adding restricted models without all four are out of scope.

### No restriction-class relaxations in the bundled baseline without review

The bundled baseline registry can tighten a model's posture (status `available → withdrawn_no_new_downloads → legally_blocked`, `commercial_use_restriction_known` False → True, `requires_explicit_ack` False → True) at any time with maintainer approval. **Relaxing** any of those (going from restricted to permissive, or from blocked to available) requires:

- A GitHub issue with the written rationale, discussed before the PR
- Maintainer approval on the PR
- Updated tests pinning the new policy

Relaxations delivered through the remote-registry overlay follow the signed-manifest rules in [Signed remote registry overlay](#signed-remote-registry-overlay).

### No bypassing the download chokepoint

Every model download must route through `bpp.utils.download.download_file()`. A direct `urllib.request.urlretrieve`, `requests.get(...).content`, `wget` call, or any other path that bypasses the chokepoint is a release blocker. The chokepoint runs the registry policy gate (status, click-through acceptance, use context) before the network call, enforces the 120 s timeout, and verifies SHA-256 when the caller passes the expected hash.

In-process model loading also has a single entry point: `ModelSingleton` from `bpp/scoring/model_base.py`. A new model that hand-rolls a `globals()` + `threading.Lock` is a review finding — the singleton wraps the load and routes its download through `download_file()` with the entry's `registry_id`. (YuNet / SFace / dlib are exempt; they predate the singleton.)

Third-party packages that auto-download models on import are intercepted by `bpp.registry.install_third_party_interceptions()` once `bpp.registry` is imported. If you add a new dependency that auto-downloads, register it in `KNOWN_AUTO_DOWNLOADERS` (see `bpp/registry/download_chokepoint.py`) in the same PR.

### Acceptance text is versioned, hashed, and append-only

The `CANONICAL_DISCLAIMER`, `BYOM_DISCLAIMER`, and `BPP_POSTURE_STATEMENT` strings in `bpp.registry.disclaimers`, and the dialog text in `bpp.registry.use_context` (including the checkbox wording), are user-facing legal text. Any edit must:

- Bump the matching version constant (`CANONICAL_DISCLAIMER_VERSION`, `BYOM_DISCLAIMER_VERSION`, `USE_CONTEXT_TEXT_VERSION`) so existing acceptance-log rows are distinguishable from new ones. `tests/registry/test_model_registry.py::TestDisclaimerVersionLock` fails if the canonical or BYOM text changes without a new pinned version.
- Pass the surface-parity test (`tests/registry/test_surface_parity.py`) — the Settings → Models banner renders `BPP_POSTURE_STATEMENT` directly, the README License section must contain its opening sentence and closing clause, and the canonical disclaimer shown in the in-app dialog must keep its key phrases
- Not retroactively change the meaning of an already-shipped version — add a new version instead

The acceptance log is append-only by design. Code that rewrites or deletes acceptance rows is a release blocker.

## Signed remote registry overlay

The remote-registry overlay (`bpp.registry.remote_registry`) lets bppicker fetch upstream license / status changes without a new release. The fetch:

- Is HTTPS-only and restricted to an allowlisted host (`arkalogy.github.io`)
- Re-checks the allowlist on every redirect hop
- Caps responses at 1 MB and times out at 10 s (overridable via `BPP_REMOTE_REGISTRY_TIMEOUT`)
- Can be disabled entirely via `BPP_DISABLE_REMOTE_REGISTRY=1`
- Falls back to the bundled baseline on any failure (network, decode, verification), logging a warning

For local development, `BPP_REMOTE_REGISTRY_URL` overrides the manifest URL and `BPP_REMOTE_REGISTRY_INSECURE=1` lifts the HTTPS and host-allowlist checks. Neither one skips signature verification.

Every published manifest must:

- Carry at least one Ed25519 signature from a key in `bpp.registry.trusted_keys.TRUSTED_KEYS`; any signature that fails to verify rejects the whole manifest
- Meet the signature **quorum** (`overlay.DUAL_SIG_REQUIREMENT`, counted by *distinct* key) to relax any entry's `commercial_use_restriction_known`, `requires_explicit_ack`, or `bppicker_commercial_default_allowed`, or to introduce a new restricted or click-through entry
- Carry a `manifest_version`, a positive integer strictly higher than the previously published manifest's (the publishing script checks this against the live manifest). Each machine remembers the highest version it has applied per registry URL (a small state file under the cache directory, `bpp.registry.manifest_rollback`) and refuses anything lower, so a replayed older signed manifest cannot bring back a state the maintainer has since tightened. A manifest without the field is refused outright. A refused manifest, like a failed fetch, leaves the bundled baseline; keeping the last applied manifest on disk for those cases is planned.
- Be verified end-to-end via `bpp model registry verify <path>` before publication

**Current state of the quorum.** One signing key is trusted today, and `DUAL_SIG_REQUIREMENT` is **1** to match. In practice that means any manifest signed by that one key can relax a restriction or add a new restricted or click-through entry. The dual-signature mechanism is built in: the verifier counts distinct keys (one key signing twice still counts once), so requiring two keys means adding a second key to `TRUSTED_KEYS` and raising `DUAL_SIG_REQUIREMENT` to 2 in the same release. Neither happens automatically.

Two narrower points about what the overlay checks:

- **Status is not quorum-gated.** A verified manifest can change an entry's `status` in either direction, including moving a `legally_blocked` entry back to `available` — but only a newer manifest can, never a replayed older one (see `manifest_version` above).
- **The runtime reclassification lock is in-process only.** `bpp.registry.policy.assert_no_silent_reclassification` refuses a permissive version of an id that was applied as restricted earlier in the same process. Built-in entries are not seeded into it, so for built-ins the quorum is the only check.

Trusted keys live in source (`bpp/registry/trusted_keys.py`). Rotating a key means shipping a new BPP release with an updated tuple.

## CODEOWNERS

`.github/CODEOWNERS` assigns the maintainer as owner of every path, with these license-sensitive paths listed explicitly:

- `bpp/registry/` — registry schema, policies, acceptance log, disclaimers
- `bpp/scoring/face_*.py` and `bpp/scoring/*_embed*.py` — face embedder + detector code and registrations, CLIP embedding
- `bpp/scoring/model_base.py` — model singleton
- `bpp/utils/download.py` — the download chokepoint
- `MODEL_POLICY.md` and `.github/CODEOWNERS` — this policy
- `.github/workflows/` and `pyproject.toml` — CI and packaging

GitHub uses the file to request the owner's review automatically on PRs that touch these paths. Branch protection on `main` does not currently require code-owner approval or passing CI, so the file routes reviews; it doesn't block merges by itself. Project policy is that changes to these paths are reviewed and approved by the maintainer before merge.

If a contributor genuinely needs to edit one of these paths, the right flow is: open an issue first, get sign-off on the change, then open the PR referencing the issue. Skipping the issue means the PR sits until the maintainer has time to re-derive the context.

## Test gates

These tests are part of the regular `pytest` suite. CI runs them on pull requests to `main` (the release gate); run them locally before opening any PR:

- `tests/registry/test_default_safety.py` — SFace is the registry's face-embedder default; no restricted entry is marked default; the bundled-weights guard catches weight files
- `tests/registry/test_download_chokepoint.py` — known auto-downloaders are intercepted. The `TestRealInsightFaceChokepoint` class runs only when `insightface` is installed, which the CI environment doesn't do.
- `tests/registry/test_signed_manifest.py` — Ed25519 sign/verify round-trip, tamper and wrong-key rejection, one key signing twice counting once, and the quorum on relaxations and new restricted entries. The "below quorum is refused" cases are skipped while `DUAL_SIG_REQUIREMENT` is 1 (there is no valid manifest below a quorum of 1) and run once it is raised.
- `tests/registry/test_surface_parity.py` — the posture statement reaches every required surface (see above); README and CHANGELOG don't name the AdaFace or buffalo_* face models
- `tests/registry/test_removal.py` — schema v41 `producing_model_id` column + index; purge counts and deletes only matching rows and skips NULL rows; built-in entries can't be removed. (The `bpp model remove` CLI refusing to run without `--purge-derived` / `--keep-derived` is implemented in `bpp/commands/model_commands.py` but has no test yet.)
- `tests/registry/test_model_policy.py` — this file names each mechanism it governs, CODEOWNERS lists the license-sensitive paths, and git tracks no banned weight files

If you add a new gate to the registry, add the test that pins it in the same PR. A registry rule without an enforcing test rots within a quarter.

## Summary

The policy is paranoid by design. The cost of an over-flagged PR is a conversation; the cost of an unreviewed restricted model entering the bundled baseline is real legal exposure with a real takedown cost. We pay the conversation cost willingly.

If anything here is unclear or seems wrong for a specific case, open an issue that references this policy before opening the PR.
