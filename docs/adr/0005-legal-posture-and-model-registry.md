# ADR 0005 — Legal posture and the model registry

**Status.** Accepted. Implemented in `bpp/registry/` (Mar–Jun 2026).

**Context.** Best Photo Picker is MIT-licensed and intends to stay so. Its
core features — face clustering, semantic search, pet detection, content
filtering, AI object removal — depend on ML models whose **weights** are
governed by separate, often non-permissive, upstream licenses:

- InsightFace's `buffalo_*` ArcFace bundles are released under
  "research and non-commercial use only" terms.
- Ultralytics YOLOv11 ships under AGPL-3.0 weights / Enterprise alternative.
- NudeNet's repository and weights are AGPL-3.0.
- LaMa inpainting weights are research-only (CC-BY-NC-SA).
- OpenAI CLIP ViT-B/32 ships under MIT (per the openai/CLIP repository).
- OpenCV Zoo SFace (the default face embedder) ships under Apache 2.0.
  dlib's code is Boost, but its 68-point landmark model was trained on
  iBUG 300-W, whose creators ask that it not be used commercially, so
  BPP treats dlib's weights as restricted.

A naive implementation would: (a) bundle weights in the wheel, (b) auto-
download on first analyze, (c) surface a single "MIT — free for commercial
use" claim. All three are wrong:

- **Bundling** infects MIT distribution by attaching non-permissive
  artifacts to a permissive code archive. A downstream redistributor
  picks up the contamination silently.
- **Auto-download without consent** makes BPP a vehicle for the user to
  unwittingly accept upstream terms they never saw. In any jurisdiction
  with BIPA-style biometric-data regulation (Illinois BIPA, Colorado
  HB24-1130 effective Jul 2025, Texas CUBI), this also exposes the user
  to liability — the code that pulls the model is the code that
  produces biometric data.
- **"MIT and free for commercial use"** mixes code license with model
  license. A reader reasonably concludes the *whole product* is
  commercial-safe. A commercial user who acted on that claim would have
  legitimate cause to be upset.

Refusing to use these models at all is a credible non-decision — but it
guts the product. The face-clustering experience SFace provides on its own
is materially worse than InsightFace's `buffalo_s`. The trade-off is real
and worth surfacing to the user with a clean opt-in path, not papering
over.

**Decision.** A structured model registry mediates every model the user
can touch, with a click-through gate for restricted-license entries, a
signed remote overlay for upstream takedowns, and a hard-block in self-
declared commercial mode. The pieces:

"Restricted" here means a registry entry with
`commercial_use_restriction_known=True` (upstream terms restrict
commercial use). A "click-through" entry is one with
`requires_explicit_ack=True`; every restricted entry is one, and so are
permissive attribution-licensed models such as the default SFace and
YuNet, the MediaPipe models and OpenCV's Haar cascade. Attribution-only
entries are accepted together in one step before the first analysis;
restricted entries keep their own dialog. The same definitions are used in `MODEL_POLICY.md`.

| Component | What it does |
|-----------|--------------|
| Registry schema | `ModelEntry` schema, canonical disclaimer text, status enum (`available` / `deprecated` / `withdrawn_no_new_downloads` / `legally_blocked`), audit fields (`ack_text_version`, `ack_text_sha256`, `terms_permalink_url`) |
| Permissive default + no bundled restricted weights | SFace (permissive) is the default embedder and a test fails if a restricted entry is marked default; a CI check on PRs to `main` fails if any weight file other than the allowlisted Apache-2.0 BlazeFace detector is present in the source tree or the wheel |
| Download chokepoint | Every BPP model download goes through `download_file()`, which runs the policy gate first; once `bpp.registry` is imported, `insightface.utils.storage.download` (and friends) are patched to refuse unsanctioned auto-downloads |
| Click-through dialog | 4 required checkboxes (not commercial / MIT doesn't grant rights / downloaded from a third-party host, not Arkalogy / no paid use without separate rights); per-user `~/.config/bpp/model-acceptance.jsonl` outside the library directory |
| Use-context gate | First-launch choice (Personal / Research / Commercial / Unspecified); restricted models are hard-blocked in commercial mode unless the user asserts separate rights per model |
| Bring Your Own Model | User-supplied ONNX files, with a separate BYOM disclaimer + checkbox |
| Model removal | Derived-data purge (face embeddings tagged with `producing_model_id`); `bpp model remove` and `POST /api/v1/model-registry/removal` both refuse to run without an explicit purge-or-keep choice |
| Signed remote registry | Ed25519-signed manifest fetched from an allowlisted GitHub Pages domain; restriction-class relaxations and new restricted or click-through entries need a quorum of distinct trusted keys (`DUAL_SIG_REQUIREMENT`, currently 1 because one key is trusted); each machine keeps the highest `manifest_version` it has applied and refuses a replayed older manifest |
| Surface parity | One constant (`BPP_POSTURE_STATEMENT`) is rendered directly in the Settings → Models banner; tests check that the README (also the PyPI description) carries its opening sentence and closing clause and that the canonical disclaimer printed by the dialog and `bpp model accept` keeps its key phrases; restricted-model names are kept out of README and CHANGELOG |
| Contributor policy | `MODEL_POLICY.md`, `CONTRIBUTING.md`, and `CODEOWNERS` routing reviews of `bpp/registry/**`, `bpp/scoring/face_*.py`, `bpp/scoring/*_embed*.py`, `bpp/utils/download.py` to the maintainer |

The acceptance row records every load-bearing field for an audit
chain: `model_id`, `ack_text_version` + `ack_text_sha256` (pin the dialog
wording), `terms_permalink_url` (pin the upstream license at a specific
commit), `use_context_at_acceptance`, `separate_rights_asserted`,
per-checkbox responses (schema v2), `accepted_at`. The file is created
with mode `0o600` (privacy-by-design on shared systems).

**Posture statement** — `BPP_POSTURE_STATEMENT` in
`bpp/registry/disclaimers.py` reads, verbatim:

> Arkalogy will not monetize, sell, or market Best Photo Picker for
> commercial workflows. Because the source code is MIT-licensed, third
> parties may still use it commercially; restricted-model access is
> separately controlled by model-specific terms and app-level gates
> (commercial-use gate, hard-block, click-through acknowledgment).
> Optional third-party models may have separate licenses, including
> non-commercial or research-only restrictions — commercial users must
> select commercial-safe models or provide their own licensed weights.

The wording distinguishes Arkalogy's *choice* not to commercialize
from the *rights* the MIT license grants downstream.

**Consequences.**

*Gained:*
- A defensible MIT distribution: no restricted weights enter the
  wheel or get hosted on Arkalogy infrastructure. The only model file
  in the wheel is the Apache-2.0 BlazeFace short-range detector,
  credited in `NOTICE.txt`. `scripts/check_no_bundled_weights.py`
  checks the source tree and the built wheel in CI on PRs to `main`.
- A click-through that BPP's own code paths can't skip. Every
  BPP download runs the policy gate before the network call, and once
  `bpp.registry` is imported, `insightface`'s own auto-download is
  patched to raise `BlockedAutoDownloadError` outside a sanctioned
  download. Inside BPP, the click-through dialog (or
  `bpp model accept`) is the only way to unlock a restricted model. A
  separate Python process that never imports BPP isn't affected.
- An audit trail that survives upstream churn. The
  `terms_permalink_url` field on every restricted entry pins the
  upstream license to a specific commit; the acceptance log captures
  the SHA-256 of the dialog text the user actually saw, with a CI
  test (`TestDisclaimerVersionLock`) that fails if the text mutates
  without a version bump.
- A real takedown mechanism. The signed remote manifest can transition
  any entry to `legally_blocked`, and the registry status enum is
  consumed by both the load gate (`bpp/registry/policy.py`) and the
  download gate (`bpp/web/bp_models.py:_enforce_download_status_gate`).
  A `legally_blocked` entry can't be downloaded or loaded, including
  copies already on disk; a withdrawn restricted entry is refused the
  same way.
- A privacy-by-design data-retention story. Removing a model can
  purge the derived face embeddings tagged with its
  `producing_model_id` (the CLI and the HTTP endpoint both require an
  explicit purge-or-keep choice). A status change
  on its own, such as a takedown, doesn't purge them.

*Given up:*
- Restricted models are never the default. SFace's clustering quality
  is materially below `buffalo_s` out of the box.
- Some user friction. A first-time `buffalo_s` user clicks through a
  4-checkbox dialog before they can analyze faces. The dialog is
  designed to be readable in under 60 seconds.
- A meaningful maintenance surface: the `/api/v1/model-registry/*`
  endpoints, 18 Python modules under `bpp/registry/`, and 17 test
  files under `tests/registry/`. `CODEOWNERS` routes changes to the
  load-bearing paths to the maintainer for review.

*Tests that enforce this ADR.*

- `tests/registry/test_default_safety.py` — SFace remains default; no
  restricted entry has `default_for_kind=True`.
- `tests/registry/test_download_status_gate.py` — `LEGALLY_BLOCKED`
  and `WITHDRAWN_NO_NEW_DOWNLOADS` entries cannot be re-downloaded.
- `tests/registry/test_download_chokepoint.py::TestRealInsightFaceChokepoint`
  — patches survive against the real `insightface` package, not just
  synthetic fakes (runs only where `insightface` is installed; CI
  doesn't install it).
- `tests/registry/test_acceptance.py::TestPermalinkValidation` — every
  restricted built-in has a non-empty `terms_permalink_url`.
- `tests/registry/test_acceptance.py::TestAcceptanceLogPermissions` —
  log file is created mode `0o600`, parent directory `0o700`.
- `tests/registry/test_model_registry.py::TestDisclaimerVersionLock` —
  canonical + BYOM disclaimer text hashes match the pinned versions.
- `tests/registry/test_signed_manifest.py` — signature verification,
  distinct-key counting, and the quorum on restriction-class
  relaxations (the below-quorum cases run once the quorum is above 1).
- `tests/registry/test_manifest_rollback.py` — a manifest needs a
  positive `manifest_version`; the startup overlay refuses one below
  the highest applied on the machine and records a higher one.
- `tests/registry/test_surface_parity.py` — the Settings banner
  renders the posture constant, the README carries its opening and
  closing, the canonical disclaimer keeps its key phrases; README and
  CHANGELOG don't name restricted models.

**Out of scope.**

- BPP itself never being commercialized as an Arkalogy product. That's
  a business decision, not an ADR. The MIT license stands; this ADR
  does not constrain downstream commercial use of BPP's code.
- Per-model recommendations. The registry surfaces every model with
  its upstream terms; users decide which to accept.
- Re-litigating the choice of upstream models. New permissive face
  embedders (e.g. a future weights-MIT release of AdaFace) would land
  via the same registry surface; the gate machinery is model-agnostic.
