"""Canonical legal disclaimers reused across every surface.

The disclaimer and posture texts are authored here. The Settings
banner, click-through dialog, CLI, and registry read them from this
module; the README (and the PyPI long description built from it)
carries a copy that :mod:`tests.registry.test_surface_parity` checks. Bespoke per-surface
wording is exactly the failure mode the "warning parity across
surfaces" rule protects against.

The version constant lets the click-through dialog snapshot which
text the user accepted. The SHA-256 derivation lets the acceptance
log record an immutable fingerprint of the exact bytes the dialog
showed.

Why the text reads the way it does

  The core distribution statement is:

    "Best Photo Picker does not redistribute or bundle this model. If selected,
    the model is downloaded by the user's local installation from a
    third-party host (not from Arkalogy), subject to upstream model terms."

  "Third-party host" rather than "the upstream provider" because not
  every download URL is the original publisher's: LaMa's weights,
  for example, come from the simple-lama-inpainting release assets.

  Combined with the monetization statement (which distinguishes
  Arkalogy's own stance from third-party MIT rights), this
  is the legally-accurate baseline used wherever a model's license
  posture must be communicated.

  Compressed dialog wording (same legal substance, fewer
  sentences) lives alongside the full version. Both are
  authored here so the compressed form cannot drift from the full
  form: the dialog renders the compressed text with a "Read full
  terms" link that expands to the full text.

Versioning

  Whenever the canonical text changes, increment
  :data:`CANONICAL_DISCLAIMER_VERSION`. Existing acceptance-log rows
  retain their snapshot of the old version (the acceptance log
  records the version the user actually saw).

Why the full text is built rather than a single string constant

  Authoring it as paragraphs joined by ``"\\n\\n"`` keeps the
  individual sentences greppable for editorial checks. A future
  maintainer touching one sentence is less likely to garble the
  surrounding text.
"""

from __future__ import annotations

import hashlib

#: Version identifier for the canonical disclaimer. Increment when
#: any of :data:`CANONICAL_DISCLAIMER` paragraphs changes. The
#: acceptance log records this string so we can later tell which
#: version of the wording a given user agreed to.
#:
#: v3: paragraph 1 says "from a third-party host (not from Arkalogy)"
#: instead of "directly from the upstream provider".
CANONICAL_DISCLAIMER_VERSION = "canonical-disclaimer-v3"


#: The full canonical disclaimer text. Paragraphs separated by
#: blank lines. This is the legally-accurate baseline reused
#: everywhere a model's license posture must be communicated.
CANONICAL_DISCLAIMER = "\n\n".join(
    (
        # Paragraph 1 — Arkalogy's distribution posture.
        "Best Photo Picker does not redistribute or bundle this model. If selected, "
        "the model is downloaded by the user's local installation from a "
        "third-party host (not from Arkalogy), subject to upstream model terms.",
        # Paragraph 2 — Arkalogy's monetization stance ("BPP will
        # never be commercialized" would be legally inaccurate
        # because MIT permits downstream commercial use of the code;
        # this wording distinguishes Arkalogy's choice from
        # third-party rights).
        "Arkalogy will not monetize, sell, or market Best Photo Picker for commercial "
        "workflows. However, because the code is MIT-licensed, third parties "
        "may still use the code commercially. Restricted-model access is "
        "separately controlled by model-specific terms and app-level gates "
        "(commercial-use gate, hard-block, click-through acknowledgment).",
        # Paragraph 3 — the source-vs-models distinction, instead of
        # a blanket "MIT and free for commercial use" framing.
        "Best Photo Picker's source code is MIT-licensed. Optional third-party models "
        "may have separate licenses, including non-commercial or "
        "research-only restrictions. Commercial users must select "
        "commercial-safe models or provide their own licensed weights.",
    )
)


#: Compressed two-sentence form of the disclaimer used inside the
#: in-app click-through dialog so users actually read it.
#: Renders alongside a "Read full terms" link expanding to
#: :data:`CANONICAL_DISCLAIMER`. Legal substance is identical; the
#: short form is for readability, not for swapping in a different
#: meaning.
CANONICAL_DISCLAIMER_COMPRESSED = (
    "Arkalogy does not monetize Best Photo Picker, but MIT permits third-party "
    "commercial use of the code. Restricted-model access is separately "
    "gated by this dialog and your commercial-use selection."
)


#: Surface-parity statement: BPP's posture on its own commercial
#: future and on third-party model licensing. The Settings → Models
#: banner renders this constant directly, and the README License
#: section (also the PyPI long description) carries it as text.
#: :mod:`tests.registry.test_surface_parity` checks that the README
#: contains its opening sentence and closing clause, and that
#: :data:`CANONICAL_DISCLAIMER` (shown in the click-through dialog)
#: keeps its key phrases.
#:
#: The statement combines the MIT/commercial framing with
#: Arkalogy's monetization stance into
#: a paragraph short enough to fit a Settings banner without losing
#: the legally-load-bearing distinctions. Keep its meaning aligned
#: with the second + third paragraph of :data:`CANONICAL_DISCLAIMER`.
BPP_POSTURE_STATEMENT = (
    "Arkalogy will not monetize, sell, or market Best Photo Picker for "
    "commercial workflows. Because the source code is MIT-licensed, "
    "third parties may still use it commercially; restricted-model "
    "access is separately controlled by model-specific terms and "
    "app-level gates (commercial-use gate, hard-block, click-through "
    "acknowledgment). Optional third-party models may have separate "
    "licenses, including non-commercial or research-only "
    "restrictions — commercial users must select commercial-safe "
    "models or provide their own licensed weights."
)


def canonical_disclaimer_sha256() -> str:
    """Return the hex SHA-256 of :data:`CANONICAL_DISCLAIMER`.

    Used by the registry's ``ack_text_sha256`` field and by the
    acceptance log to fingerprint the exact bytes a user accepted.
    Computed at call time rather than baked in as a constant so a
    routine edit of the canonical text doesn't require a second edit
    of a stale hash literal.
    """
    return hashlib.sha256(CANONICAL_DISCLAIMER.encode("utf-8")).hexdigest()


def canonical_disclaimer_compressed_sha256() -> str:
    """Return the hex SHA-256 of :data:`CANONICAL_DISCLAIMER_COMPRESSED`.

    Some downstream consumers (the click-through dialog snapshot in
    particular) hash the compressed form rather than the full form,
    because the compressed form is what the user actually read.
    """
    return hashlib.sha256(CANONICAL_DISCLAIMER_COMPRESSED.encode("utf-8")).hexdigest()


# ── BYOM (Bring Your Own Model) disclaimer ──
#
# When a user points BPP at their own ONNX file, the legal posture
# inverts: Arkalogy is not in the rights chain at all. The disclaimer
# the user signs is shorter and shifts every responsibility onto them.
# The wording says "acknowledges and agrees to comply" rather than
# "agrees to upstream's license," because there is no upstream —
# there's only the user's own model file.

#: Version identifier for the BYOM disclaimer below.
BYOM_DISCLAIMER_VERSION = "byom-disclaimer-v2"


#: Full BYOM disclaimer. Shown when the user adds a Bring-Your-Own-Model
#: entry. Shorter than :data:`CANONICAL_DISCLAIMER` because there is no
#: upstream license to gate against — the user takes on the full
#: responsibility for ensuring they have rights to the file.
BYOM_DISCLAIMER = "\n\n".join(
    (
        # Paragraph 1 — user responsibility.
        "You are responsible for ensuring you have rights to use this "
        "model file for your intended purpose. Best Photo Picker does not verify "
        "or grant rights to user-provided model files.",
        # Paragraph 2 — Arkalogy's non-distribution posture. Even when
        # the user supplies the file, BPP's own MIT license does not
        # extend to it, and the rights to use the file are governed by
        # whatever license the user obtained from its source.
        "Best Photo Picker's source code is MIT-licensed. The license on the "
        "model file you provide is separate from Best Photo Picker and may "
        "restrict commercial, redistributive, or other uses. Verify "
        "the terms with the file's source before using it.",
    )
)


#: Compressed BYOM disclaimer for the click-through dialog header
#: (same substance, fewer sentences). The full text is
#: surfaced behind a "Read full terms" link.
BYOM_DISCLAIMER_COMPRESSED = (
    "You are responsible for ensuring you have rights to use this "
    "model file. Best Photo Picker does not verify or grant any rights to "
    "user-provided files."
)


def byom_disclaimer_sha256() -> str:
    """Return the hex SHA-256 of :data:`BYOM_DISCLAIMER`.

    Used by the BYOM acceptance log row to fingerprint the exact
    bytes a user agreed to when they added a file. A future edit of
    the BYOM disclaimer bumps :data:`BYOM_DISCLAIMER_VERSION`; the
    hash changes too, and existing BYOM entries' acceptance rows
    become invalid (the policy will re-prompt the user to re-accept
    the new wording at next use).
    """
    return hashlib.sha256(BYOM_DISCLAIMER.encode("utf-8")).hexdigest()


def byom_disclaimer_compressed_sha256() -> str:
    """Return the hex SHA-256 of :data:`BYOM_DISCLAIMER_COMPRESSED`."""
    return hashlib.sha256(BYOM_DISCLAIMER_COMPRESSED.encode("utf-8")).hexdigest()


# ── Permissive-attribution disclaimer ──────────────────────────────
#
# Used for models distributed under permissive open-source licenses
# that AREN'T MIT — Apache 2.0, BSD, Boost, etc. These licenses
# permit free use including commercial use, but they still impose
# attribution obligations the user should acknowledge once before
# the model is downloaded.
#
# The strictest-defensible legal posture draws the line at MIT: MIT entries pass through
# silently; everything else requires a one-time click-through so the
# user has explicitly seen the license name and the fact that
# attribution duties attach when they redistribute or build a
# commercial product on top.
#
# The disclaimer is intentionally short — the user is not being
# asked to agree to a research-only restriction or a hard-block; the
# acknowledgment is simply that they know the attribution duties
# exist and apply to their use case.

#: Version identifier for the permissive-attribution disclaimer.
PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION = "permissive-attribution-v1"


#: Full permissive-attribution disclaimer. Paragraphs separated by
#: blank lines. Shown alongside a "Read full terms" link expanding
#: to the upstream license text the entry's ``terms_permalink_url``
#: points at.
PERMISSIVE_ATTRIBUTION_DISCLAIMER = "\n\n".join(
    (
        # Paragraph 1 — what kind of license + what it allows.
        "This model is distributed under a permissive open-source license "
        "(e.g. Apache 2.0, BSD, or Boost Software License). Permissive "
        "licenses allow free use, including commercial use.",
        # Paragraph 2 — what permissive does NOT mean.
        "Permissive licenses are not obligation-free. They typically require "
        "preserving the original copyright notice and the license text when "
        "the model is redistributed or shipped as part of a product. Apache "
        "2.0 additionally requires preserving any NOTICE file from the "
        "upstream project.",
        # Paragraph 3 — user responsibility.
        "If you redistribute this model or build a product that includes it, "
        "you remain responsible for honouring the attribution requirements "
        "of the upstream license. Best Photo Picker downloads the model on "
        "your behalf but does not assume those obligations for you.",
    )
)


#: Compressed two-sentence form shown in the click-through dialog so
#: users actually read it. Renders alongside a "Read full terms" link
#: expanding to :data:`PERMISSIVE_ATTRIBUTION_DISCLAIMER`.
PERMISSIVE_ATTRIBUTION_DISCLAIMER_COMPRESSED = (
    "This model uses a permissive license that allows commercial use but "
    "requires attribution (copyright notice + license text) if you "
    "redistribute it or ship a product that includes it."
)


def permissive_attribution_disclaimer_sha256() -> str:
    """Return the hex SHA-256 of
    :data:`PERMISSIVE_ATTRIBUTION_DISCLAIMER`. Used by the registry's
    ``ack_text_sha256`` field on permissive-attribution entries and
    by the acceptance log to fingerprint the exact bytes a user
    agreed to."""
    return hashlib.sha256(PERMISSIVE_ATTRIBUTION_DISCLAIMER.encode("utf-8")).hexdigest()


def permissive_attribution_disclaimer_compressed_sha256() -> str:
    """Return the hex SHA-256 of
    :data:`PERMISSIVE_ATTRIBUTION_DISCLAIMER_COMPRESSED`."""
    return hashlib.sha256(PERMISSIVE_ATTRIBUTION_DISCLAIMER_COMPRESSED.encode("utf-8")).hexdigest()
