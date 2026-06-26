#!/usr/bin/env python3
"""Sign a registry manifest with one or two Ed25519 private keys.

The companion to the verifier at ``bpp.registry.signed_manifest``.
Reads an unsigned (or already-signed — we re-sign) manifest JSON,
attaches one signature per ``--key`` flag, and writes the result.

Single-signature usage (routine status tightening, ack-text bump,
new permissive entry):

    python scripts/sign_registry_manifest.py \\
        --in unsigned.json --out signed.json \\
        --key ~/.config/bpp/signing-keys/arkalogy-primary-2026-06.private.key

Dual-signature usage (restriction relaxation, new restricted entry):

    python scripts/sign_registry_manifest.py \\
        --in unsigned.json --out signed.json \\
        --key ~/.config/bpp/signing-keys/arkalogy-primary-2026-06.private.key \\
        --key ~/.config/bpp/signing-keys/arkalogy-secondary-2026-06.private.key

The key file format is a single base64-encoded raw 32-byte Ed25519
private key (``priv.private_bytes_raw()`` then base64). The key_id
slug is derived from the filename basename minus the ``.private.key``
suffix.

Verify locally before publishing:

    bpp model registry verify signed.json

Exits 0 on success, 1 on any failure (file IO, decode, no keys, etc.).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bpp.registry import sign_manifest


def _load_private_key(path: Path) -> tuple[str, Ed25519PrivateKey]:
    raw = path.read_text(encoding="ascii").strip()
    try:
        key_bytes = base64.b64decode(raw)
    except Exception as exc:
        raise SystemExit(f"{path}: not valid base64: {exc}") from exc
    if len(key_bytes) != 32:
        raise SystemExit(
            f"{path}: expected 32 bytes of raw Ed25519 private key, got {len(key_bytes)}"
        )
    priv = Ed25519PrivateKey.from_private_bytes(key_bytes)
    # Slug derived from filename: "arkalogy-primary-2026-06.private.key"
    # -> "arkalogy-primary-2026-06".
    slug = path.name
    for suffix in (".private.key", ".key", ".priv"):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            break
    return slug, priv


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sign a bppicker registry manifest with one or two Ed25519 keys."
    )
    parser.add_argument(
        "--in",
        dest="in_path",
        required=True,
        help="Path to the unsigned manifest JSON",
    )
    parser.add_argument(
        "--out",
        dest="out_path",
        required=True,
        help="Path to write the signed manifest JSON",
    )
    parser.add_argument(
        "--key",
        dest="key_paths",
        action="append",
        required=True,
        help=(
            "Path to a private key file. Pass twice for dual-sig "
            "(restriction relaxation or new restricted entry)."
        ),
    )
    args = parser.parse_args()

    in_path = Path(args.in_path).expanduser()
    out_path = Path(args.out_path).expanduser()
    try:
        manifest = json.loads(in_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"cannot read {in_path}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"{in_path}: not valid JSON: {exc}", file=sys.stderr)
        return 1

    signers: list[tuple[str, Ed25519PrivateKey]] = []
    for kp in args.key_paths:
        path = Path(kp).expanduser()
        if not path.exists():
            print(f"key file not found: {path}", file=sys.stderr)
            return 1
        signers.append(_load_private_key(path))

    signed = sign_manifest(manifest, signers)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(signed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"signed {in_path} with {len(signers)} key(s); "
        f"wrote {out_path}\n"
        f"  signing keys: {', '.join(s[0] for s in signers)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
