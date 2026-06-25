# Model Policy

This document is the contributor policy for adding, modifying, or removing ML models in bppicker. It exists because a well-meaning pull request can introduce real legal exposure — a non-permissive model file checked into git, an unreviewed download URL, a restriction-class downgrade that bypasses the click-through gate. None of these are caught by a typical code review.

The rules below are mandatory for any PR that touches the model registry, the download chokepoint, or anything under `bpp/registry/`. They are enforced by a combination of tests, CODEOWNERS gating, and human review at merge.

If a rule below seems inconvenient, that is the rule working as intended.

## What this policy protects

bppicker's legal posture is built on three load-bearing claims:

1. **bppicker does not bundle or redistribute model weights.** Every model is downloaded by the user's local installation from the upstream provider on first use, subject to upstream terms.
2. **The bundled registry baseline never silently adds, removes, or relaxes a model's restriction class.** Status tightening (becoming less available) is safe; status relaxation requires an explicit signed-manifest review meeting the trusted-key quorum (see [Signed remote registry](#signed-remote-registry-overlay) below).
3. **Restricted-license models are not the default.** SFace is the permanent default face embedder. Any other embedder reaches users only via the opt-in click-through gate, with a hard-block in commercial-use mode.

Every rule in this document protects one of those three claims.

## Hard rules

### No binary model weights in the repo

The git tree contains zero `.onnx`, `.pt`, `.bin`, `.dat`, `.task`, `.safetensors`, or `.npy` files outside of `bpp/web/static/vendor/` (vendored JS / CSS only). The CI gate enforces this. Model weights live on the user's machine after first-use download — never in the repo, never in a release artifact, never in a docker image we ship.

If you need a model file to write a test, vendor a tiny synthetic fixture in the test directory only — and never call it a "model weight."

### No unreviewed model URLs

A new `source_url` or `terms_url` in the model registry must:

- Point at an HTTPS URL on a domain the maintainer has reviewed for the upstream license and provenance
- Carry the exact upstream license terms in the same PR (in the registry `license_summary` field and in `docs/` or `NOTICE.txt` if appropriate)
- Be approved by a code owner (see [CODEOWNERS](#codeowners-gating))

A PR that adds a URL without a license review attached will be closed without merge.

### No new restricted models without an issue first

A "restricted model" is any entry where `commercial_use_restriction_known` is True or `requires_explicit_ack` is True. Adding one to the bundled baseline requires:

1. A GitHub issue describing why the model is being added, the upstream license, the commercial-use restriction, and which acceptance text version users will sign
2. Maintainer approval on the issue before the PR is opened
3. Code owner approval on the PR itself
4. New tests pinning the entry's policy decisions (`requires_explicit_ack`, `commercial_use_restriction_known`, the `ack_text_kind` discriminator) so a future edit of the registry can't silently flip them

PRs adding restricted models without all four are out of scope.

### No restriction-class relaxations in the bundled baseline

The bundled baseline registry can tighten a model's posture (status `available → withdrawn_no_new_downloads → legally_blocked`, `commercial_use_restriction_known` False → True, `requires_explicit_ack` False → True) at any time with a single owner approval. **Relaxing** any of those (going from restricted to permissive, or from blocked to available) requires:

- Maintainer-level discussion documented in a GitHub issue
- Two code owners' approvals on the PR
- New tests pinning the new policy

The same signed-quorum requirement applies at runtime to the remote-registry overlay (see [Signed remote registry overlay](#signed-remote-registry-overlay)).

### No bypassing the download chokepoint

Every model download must route through `bpp.utils.download.download_file()`. A direct `urllib.request.urlretrieve`, `requests.get(...).content`, `wget` call, or any other path that bypasses the chokepoint is a release blocker. The chokepoint enforces SHA-256 verification, the 120 s timeout, and the registry-coordinated click-through gate.

In-process model loading also has a single entry point: `ModelSingleton` from `bpp/scoring/model_base.py`. A new model that hand-rolls a `globals()` + `threading.Lock` is a review finding — the singleton wraps both the load and the download chokepoint registration. (YuNet / SFace / dlib are exempt; they predate the singleton.)

Third-party packages that auto-download models on import are intercepted at `bpp.registry.install_third_party_interceptions()`. If you add a new dependency that auto-downloads, register it in `KNOWN_AUTO_DOWNLOADERS` (see `bpp/registry/download_chokepoint.py`) in the same PR.

### Acceptance text is versioned, hashed, and append-only

The `CANONICAL_DISCLAIMER`, `BYOM_DISCLAIMER`, and `BPP_POSTURE_STATEMENT` strings in `bpp.registry.disclaimers` are user-facing legal text. Any edit must:

- Bump the version constant (`CANONICAL_DISCLAIMER_VERSION` etc.) so existing acceptance-log rows are distinguishable from new ones
- Pass the surface-parity test (`tests/registry/test_surface_parity.py`) — the new wording must appear verbatim in every required surface (README License section, Settings → Models banner, in-app dialog)
- Not retroactively change the meaning of an already-shipped version — append a new version instead

The acceptance log is append-only by design. Code that rewrites or deletes acceptance rows is a release blocker.

## Signed remote registry overlay

The remote-registry overlay (`bpp.registry.remote_registry`) lets bppicker fetch upstream license / status changes without a new release. The fetch:

- Is HTTPS-only with a hard-coded host allowlist (`arkalogy.github.io`)
- Re-checks the allowlist on every redirect hop
- Caps responses at 1 MB and times out at 10 s (overridable via `BPP_REMOTE_REGISTRY_TIMEOUT`)
- Can be disabled entirely via `BPP_DISABLE_REMOTE_REGISTRY=1`
- Falls back silently to the bundled baseline on any failure (network, decode, verification)

Every published manifest must:

- Carry at least one Ed25519 signature from a key in `bpp.registry.trusted_keys.TRUSTED_KEYS`
- Meet the trusted-key **quorum** (`overlay.DUAL_SIG_REQUIREMENT`, counted by *distinct* key) to relax any entry's `commercial_use_restriction_known`, `requires_explicit_ack`, or `bppicker_commercial_default_allowed`, or to introduce a new restricted entry. **Interim:** the quorum is **1** while BPP is a single-operator project with one signing key, so the primary key alone can authorize these changes today. The design target is the **dual-signature** quorum of 2 distinct keys (so a single key compromise can't authorize a downgrade); the quorum is raised back to 2 the moment a second, cold-stored key is provisioned. The verifier counts distinct keys, so one key signing twice can never satisfy a quorum above 1.
- Be verified end-to-end via `bpp model registry verify <path>` before publication

Trusted keys live in source (`bpp/registry/trusted_keys.py`). Rotation = a new BPP release with an updated tuple. The source ships one real primary key today; the secondary (dual-sig) slot is reserved until a real cold-stored keypair is escrowed.

## CODEOWNERS gating

The repo's `.github/CODEOWNERS` file gates the following paths behind owner review:

- `bpp/registry/**` — registry schema, policies, acceptance log, disclaimers
- `bpp/scoring/*_embed*.py` and `bpp/scoring/face_*` — face embedder + detector registrations
- `bpp/scoring/model_base.py` — model singleton
- `bpp/utils/download.py` — the download chokepoint
- `MODEL_POLICY.md` — this file

A PR touching any of those without owner approval cannot merge. (GitHub enforces this when branch protection is enabled with "Require review from Code Owners.")

If a contributor genuinely needs to edit a gated path, the right flow is: open an issue first, get sign-off on the change, then open the PR referencing the issue. Skipping the issue means the PR sits until an owner has time to re-derive the context.

## Test gates

The following tests run on every PR and are non-negotiable:

- `tests/registry/test_default_safety.py` — SFace is the permanent face-embedder default; CI guard rejects any other default
- `tests/registry/test_download_chokepoint.py` — known auto-downloaders are intercepted; the chokepoint cannot be bypassed
- `tests/registry/test_signed_manifest.py` — Ed25519 verifier + signed-quorum requirement on relaxations (distinct-key count; quorum re-tightens to 2 automatically)
- `tests/registry/test_surface_parity.py` — posture statement appears verbatim in every required surface; marketing copy contains no restricted-model names
- `tests/registry/test_removal.py` — schema v41 column + nullable + index; purge skips NULL rows; CLI fails closed without `--purge-derived` / `--keep-derived`

If you add a new gate to the registry, add the test that pins it in the same PR. A registry rule without an enforcing test rots within a quarter.

## Summary

The policy is paranoid by design. The cost of an over-flagged PR is a conversation; the cost of an unreviewed restricted model entering the bundled baseline is real legal exposure with a real takedown cost. We pay the conversation cost willingly.

If anything here is unclear or seems wrong for a specific case, open an issue tagged `model-policy` and the maintainer will respond before the PR opens.
