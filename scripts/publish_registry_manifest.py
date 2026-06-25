#!/usr/bin/env python3
"""Build + sign a registry.json in one interactive step.

Companion to ``scripts/sign_registry_manifest.py``: that script
expects an unsigned manifest file + a key file (the engineering /
CI flow). This script is the *maintainer* flow — it builds the
manifest from scratch and reads the private key from stdin so it
never has to touch disk.

Usage:

    .venv/bin/python scripts/publish_registry_manifest.py \\
        [--entries path/to/entries.json] \\
        [--out path/to/registry.json] \\
        [--maintainer "Arkalogy LLC"]

Prompt: paste your private key from its secure vault. The script
verifies the pasted key derives to the primary public key in
``bpp/registry/trusted_keys.py`` so a wrong-key paste fails
immediately rather than producing an unverifiable manifest.

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
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bpp.registry.signed_manifest import (
    MANIFEST_SCHEMA_VERSION,
    canonical_manifest_bytes,
)
from bpp.registry.trusted_keys import TRUSTED_KEYS


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
    args = parser.parse_args()

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

    pasted = input(
        "Paste the private key (paste-friendly password vault), then press Enter: "
    ).strip()
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
    print(f"Wrote {args.out} ({args.out.stat().st_size} bytes)")
    print()
    print("Contents:")
    print(args.out.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
