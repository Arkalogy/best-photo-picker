#!/usr/bin/env python3
"""Build + sign a registry.json in one interactive step.

Companion to ``scripts/sign_registry_manifest.py``: that script
expects an unsigned manifest file + a key file (the engineering /
CI flow). This script is the *maintainer* flow — it builds the
manifest from scratch and reads the private key from stdin so it
never has to touch disk.

Usage:

    .venv/bin/python scripts/publish_registry_manifest.py \\
        --manifest-version N \\
        [--entries path/to/entries.json] \\
        [--out path/to/registry.json] \\
        [--maintainer "Arkalogy LLC"]

Prompt: paste your private key from its secure vault. The script
verifies the pasted key derives to the primary public key in
``bpp/registry/trusted_keys.py`` so a wrong-key paste fails
immediately rather than producing an unverifiable manifest.

``--manifest-version`` is the publication number: the live manifest's
plus one (1 when the live one predates the field). The script fetches
the live manifest and refuses a number that is not higher, because
the app keeps the highest number it has applied and refuses anything
lower — a reused or lowered number is a manifest nobody receives.

Defaults: no entries (empty overlay, baseline ships unchanged), output
at ``/tmp/registry.json``, maintainer ``"Arkalogy LLC"``. Move the
output into the ``bppicker-registry`` repo and ``git push`` to
publish via GitHub Pages.

Single-signer only. Dual-signature manifests (restriction-class
downgrades) go through ``scripts/sign_registry_manifest.py`` because
that flow needs both key files at the same moment, which Apple
Passwords can't supply.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Importing bpp.registry fetches + applies the live overlay at import
# time. This tool fetches the live manifest itself (once, below) and
# must not bump the maintainer's local anti-rollback mark as a side
# effect of a publish run.
os.environ.setdefault("BPP_DISABLE_REMOTE_REGISTRY", "1")

from bpp.registry.remote_registry import (
    DEFAULT_REMOTE_REGISTRY_URL,
    fetch_remote_manifest,
)
from bpp.registry.signed_manifest import (
    MANIFEST_SCHEMA_VERSION,
    canonical_manifest_bytes,
)
from bpp.registry.trusted_keys import TRUSTED_KEYS
from bpp.utils.json_utils import safe_json_loads


def _live_manifest_version() -> int | None:
    """The ``manifest_version`` of the published manifest: 0 when it has
    none (published before the rule), ``None`` when it can't be fetched."""
    data = fetch_remote_manifest(DEFAULT_REMOTE_REGISTRY_URL)
    if data is None:
        return None
    live = safe_json_loads(data, expected_type=dict, context="live registry manifest")
    if live is None:
        return None
    version = live.get("manifest_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return 0
    return version


def _check_against_live(new_version: int) -> int:
    """Refuse a number that is not above the live manifest's; 0 to go on."""
    live = _live_manifest_version()
    if live is None:
        print(
            "WARNING: could not fetch the live manifest to compare manifest_version; "
            f"make sure {new_version} is higher than the published one.",
            file=sys.stderr,
        )
        return 0
    if new_version <= live:
        print(
            f"--manifest-version {new_version} is not above the live manifest's "
            f"{live}; the app would refuse it. Use {live + 1} or higher.",
            file=sys.stderr,
        )
        return 2
    print(f"Live manifest_version: {live} -> publishing {new_version}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--entries",
        type=Path,
        default=None,
        help=(
            "Optional JSON file containing the overlay entries list. When "
            "omitted, the manifest ships with empty entries (no overlay)."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/registry.json"),
        help="Output path for the signed manifest. Defaults to /tmp/registry.json.",
    )
    parser.add_argument(
        "--maintainer",
        type=str,
        default="Arkalogy LLC",
        help="Maintainer label written into the manifest.",
    )
    parser.add_argument(
        "--manifest-version",
        type=int,
        required=True,
        help=(
            "Publication number, a positive integer strictly higher than the "
            "currently published manifest's. The app refuses a manifest whose "
            "number is below the highest it has applied (anti-rollback), so "
            "never reuse or lower it."
        ),
    )
    args = parser.parse_args()
    if args.manifest_version < 1:
        print("--manifest-version must be a positive integer.", file=sys.stderr)
        return 2
    if (rc := _check_against_live(args.manifest_version)) != 0:
        return rc

    if not TRUSTED_KEYS:
        print(
            "trusted_keys.py is empty; cannot sign. Re-establish the trust set first.",
            file=sys.stderr,
        )
        return 2
    primary = TRUSTED_KEYS[0]

    print(f"Signing key slot: {primary.key_id}")
    print(f"Expected public:  {primary.public_key_b64}")
    print()

    try:
        pasted = input(
            "Paste the private key (paste-friendly password vault), then press Enter: "
        ).strip()
    except EOFError:
        print("No key pasted; nothing written.", file=sys.stderr)
        return 1
    try:
        priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(pasted))
    except Exception as exc:
        print(f"Could not parse private key: {exc}", file=sys.stderr)
        return 1

    derived_pub = base64.b64encode(
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode()
    if derived_pub != primary.public_key_b64:
        print(
            "Private key does not match trusted public key for "
            f"{primary.key_id}.\n"
            f"  derived: {derived_pub}\n"
            f"  trusted: {primary.public_key_b64}",
            file=sys.stderr,
        )
        return 1
    print("OK Private key matches trusted_keys.py.")
    print()

    if args.entries is not None:
        try:
            entries = json.loads(args.entries.read_text())
        except Exception as exc:
            print(f"Could not read --entries: {exc}", file=sys.stderr)
            return 1
        if not isinstance(entries, list):
            print("--entries JSON must be a list of entry objects.", file=sys.stderr)
            return 1
    else:
        entries = []

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_version": args.manifest_version,
        "generated_at": datetime.datetime.now(datetime.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "maintainer": args.maintainer,
        "entries": entries,
        "signatures": None,
    }

    payload = canonical_manifest_bytes(manifest)
    sig_b64 = base64.b64encode(priv.sign(payload)).decode("ascii")
    manifest["signatures"] = [{"key_id": primary.key_id, "signature_b64": sig_b64}]

    args.out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    size = args.out.stat().st_size
    print(f"Wrote {args.out} ({size} bytes), manifest_version {args.manifest_version}")
    print()
    print("Contents:")
    print(args.out.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
