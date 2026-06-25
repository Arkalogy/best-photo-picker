# ADR 0005 — Legal posture and the model registry

**Status.** Accepted. Shipped across 10 implementation batches (Mar–Jun 2026).

**Context.** Best Photo Picker is MIT-licensed and intends to stay so. Its
core features — face clustering, semantic search, pet detection, content
filtering, AI object removal — depend on ML models whose **weights** are
governed by separate, often non-permissive, upstream licenses:

- InsightFace's `buffalo_*` ArcFace bundles are released under
  "research and non-commercial use only" terms.
- Ultralytics YOLOv11 ships under AGPL-3.0 weights / Enterprise alternative.
- NudeNet ships under GPL-3.0 weights.
- LaMa inpainting weights are research-only (CC-BY-NC-SA).
- OpenAI CLIP ViT-B/32 ships under the MIT-style OpenAI Model License.
- OpenCV Zoo SFace (the default face embedder) ships under Apache 2.0 —
  the one permissive option.

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
declared commercial mode. Ten implementation batches:

| Batch | Items | What it lands |
|------:|-------|---------------|
| 1 | 3, 7, 19, 20, 24 | Foundation: `ModelEntry` schema, canonical disclaimer text, status enum (`available` / `deprecated` / `withdrawn_no_new_downloads` / `legally_blocked`), evidentiary fields (`ack_text_version`, `ack_text_sha256`, `terms_permalink_url`) |
| 2 | 1, 2 | Lock SFace (permissive) as the default embedder; CI gate fails the build if any known weight file is present in the wheel |
| 3 | 18 | Download chokepoint — patch `insightface.utils.storage.download` (and friends) at import time, fail closed |
| 4 | 4, 5, 6, 13 | Click-through dialog with 4 required checkboxes (not commercial / MIT doesn't grant rights / direct upstream / no paid without separate rights); per-user `~/.config/bpp/model-acceptance.jsonl` outside the library directory |
| 5 | 15, 16 | First-launch use-context gate (Personal / Research / Commercial / Unspecified); hard-block restricted models in commercial mode unless the user asserts separate rights per model |
| 6 | 11 | Bring Your Own Model — user-supplied ONNX files, separate BYOM disclaimer + checkbox |
| 7 | 21 | Model removal flow with derived-data purge (face embeddings tagged with `model_id`; GUI default purge, CLI fail-closed without explicit flag) |
| 8 | 12, 23 | Signed remote registry — Ed25519-signed manifest fetched from an allowlisted GitHub Pages domain; dual-signature requirement for restriction-class downgrades |
| 9 | 8, 9, 10, 14, 17 | Surface-parity pass — canonical disclaimer wording lands verbatim in README, PyPI, Settings, dialog, CLI; editorial rule bans restricted-model names from commercial-targeted copy |
| 10 | 22 | `MODEL_POLICY.md`, `CONTRIBUTING.md`, `CODEOWNERS` gating `bpp/registry/**`, `bpp/scoring/*_embed*.py`, `bpp/utils/download.py` |

The acceptance row records every load-bearing field for an evidentiary
chain: `model_id`, `ack_text_version` + `ack_text_sha256` (pin the dialog
wording), `terms_permalink_url` (pin the upstream license at a specific
commit), `use_context_at_acceptance`, `separate_rights_asserted`,
per-checkbox responses (schema v2), `accepted_at`. The file is created
with mode `0o600` (privacy-by-design on shared systems).

**Item 17 wording** — the canonical posture statement reads, verbatim:

> Arkalogy will not monetize, sell, or market Best Photo Picker for
> commercial workflows. However, because the code is MIT-licensed, third
> parties may still use the code commercially. Restricted-model access is
> separately controlled by model-specific terms and app-level gates.

The earlier "BPP will never be commercialized" formulation was legally
inaccurate (MIT permits downstream commercial use of the code) and was
removed during Batch 9. The current wording distinguishes Arkalogy's
*choice* from the *rights* the license grants downstream.

**Consequences.**

*Gained:*
- A defensible MIT distribution: weights never enter the wheel, never
  ship in a `bppicker[*]` extra, never get hosted on Arkalogy
  infrastructure. The boundary is enforced by the Batch-2 CI gate.
- An enforceable click-through. The Batch-3 chokepoint patches
  `insightface` at import time so a user who tries to bypass the dialog
  by `pip install insightface && python -c "from insightface…"` still
  gets a `BlockedAutoDownloadError`. The Batch-4 dialog is the only
  resolution path.
- An evidentiary chain that survives upstream churn. The
  `terms_permalink_url` field on every restricted entry pins the
  upstream license to a specific commit; the acceptance log captures
  the SHA-256 of the dialog text the user actually saw, with a CI
  test (`TestDisclaimerVersionLock`) that fails if the text mutates
  without a version bump.
- A real takedown mechanism. The Batch-8 signed manifest can transition
  any entry to `legally_blocked` and the Batch-1 status enum is
  consumed by both the load gate (`bpp/registry/policy.py`) and the
  download gate (`bpp/web/bp_models.py:_enforce_download_status_gate`)
  to refuse new downloads + require re-assertion to use existing
  local copies.
- A privacy-by-design data-retention story. Model removal cascades to
  the derived face embeddings tagged with `model_id`, so a takedown
  doesn't leave biometric data behind.

*Given up:*
- Restricted models are never the default. SFace's clustering quality
  is materially below `buffalo_s` out of the box. The product
  documentation explicitly names this trade-off in the comparison
  section.
- Some user friction. A first-time `buffalo_s` user clicks through a
  4-checkbox dialog before they can analyze faces. The dialog is
  designed to be readable in <60 seconds; UX research did not surface
  abandonment as a real concern at this friction level.
- A meaningful maintenance surface: 11 HTTP endpoints, 9 registry
  modules, 12 test files. The `CODEOWNERS` gate on the load-bearing
  paths makes contributor mistakes catchable in review.

*Tests that enforce this ADR.*

- `tests/registry/test_default_safety.py` — SFace remains default; no
  restricted entry has `default_for_kind=True`.
- `tests/registry/test_download_status_gate.py` — `LEGALLY_BLOCKED`
  and `WITHDRAWN_NO_NEW_DOWNLOADS` entries cannot be re-downloaded.
- `tests/registry/test_download_chokepoint.py::TestRealInsightFaceChokepoint`
  — patches survive against the real `insightface` package, not just
  synthetic fakes.
- `tests/registry/test_acceptance.py::TestPermalinkValidation` — every
  restricted built-in has a non-empty `terms_permalink_url`.
- `tests/registry/test_acceptance.py::TestAcceptanceLogPermissions` —
  log file is created mode `0o600`, parent directory `0o700`.
- `tests/registry/test_model_registry.py::TestDisclaimerVersionLock` —
  canonical + BYOM disclaimer text hashes match the pinned versions.
- `tests/registry/test_signed_manifest.py` — dual-signature requirement
  on restriction-class downgrades.
- `tests/registry/test_surface_parity.py` — disclaimer wording and
  restricted-license label are identical across README, PyPI, Settings,
  dialog, CLI.

**Out of scope.**

- BPP itself never being commercialized as an Arkalogy product. That's
  a business decision, not an ADR. The MIT license stands; this ADR
  does not constrain downstream commercial use of BPP's code.
- Per-model recommendations. The registry surfaces every model with
  its upstream terms; users decide which to accept.
- The full Q&A decision tree (Q1–Q11) behind the 24-item plan. That
  lives in the gitignored `pm-face-embedder-spike.md` development log
  and is referenced here only for the maintainer's audit trail —
  external readers do not need it to understand the decision.
- Re-litigating the choice of upstream models. New permissive face
  embedders (e.g. a future weights-MIT release of AdaFace) would land
  via the same registry surface; the gate machinery is model-agnostic.
