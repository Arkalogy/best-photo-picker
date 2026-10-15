"""``bpp model accept-attribution`` — accept every attribution-licensed
model (SFace, YuNet, the MediaPipe models, the Haar cascade) in one
step, the CLI twin of the pre-analysis dialog's combined license step.

Restricted models are not included; they still need
``bpp model accept <id>``.
"""

from __future__ import annotations

import argparse
import sys

from bpp.registry import AcceptanceError, utc_now_iso
from bpp.registry.attribution_bundle import (
    confirm_attribution_bundle,
    pending_attribution_entries,
    prepare_attribution_bundle,
)
from bpp.registry.labels import plain_english_license_label
from bpp.registry.use_context_store import get_use_context


def do_model_accept_attribution(args: argparse.Namespace) -> int:
    """Show the notice and every pending model, then record one
    acceptance per model on an explicit yes (or ``--yes``)."""
    bundle = prepare_attribution_bundle(pending_attribution_entries())
    if not bundle.entries:
        print("All attribution-licensed models are already accepted.")
        return 0

    print(bundle.compressed_disclaimer)
    print()
    for e in bundle.entries:
        license_label = plain_english_license_label(e.upstream_claimed_license_class)
        print(f"  - {e.display_name} — {license_label}\n      {e.terms_url}")
    print()
    if bundle.biometric_responsibility_text:
        print(bundle.biometric_responsibility_text)
        print()

    (cb_id, cb_text), *_ = bundle.required_checkboxes
    if not args.yes:
        answer = input(f"{cb_text} [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("No acceptance recorded.", file=sys.stderr)
            return 1
    try:
        rows = confirm_attribution_bundle(
            [e.id for e in bundle.entries],
            checkbox_responses={cb_id: True},
            accepted_at=utc_now_iso(),
            use_context=get_use_context(),
        )
    except AcceptanceError as exc:
        print(f"bpp model accept-attribution: {exc}", file=sys.stderr)
        return 1
    print(f"Acceptance recorded for {len(rows)} models.")
    return 0


def add_parser(model_sub: argparse._SubParsersAction) -> None:
    p = model_sub.add_parser(
        "accept-attribution",
        help=(
            "Accept every attribution-licensed model (Apache 2.0 / BSD-style) "
            "in one step; restricted models still need `bpp model accept <id>`"
        ),
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Accept without the interactive prompt (you agree to the notice it prints)",
    )
    p.set_defaults(_model_func=do_model_accept_attribution)
