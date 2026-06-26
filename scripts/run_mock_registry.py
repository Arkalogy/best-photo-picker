#!/usr/bin/env python3
"""Local mock remote-registry server for dev / pre-release verification.

Walks the full chain — sign a manifest with the real Arkalogy keys,
serve it from localhost over plain http://, point bppicker at the
mock URL, watch the overlay apply the new entry to the in-process
registry.

Usage:

    .venv/bin/python scripts/run_mock_registry.py

Then in another terminal:

    export BPP_REMOTE_REGISTRY_URL=http://127.0.0.1:9088/registry.json
    export BPP_REMOTE_REGISTRY_INSECURE=1
    .venv/bin/bpp model list

You should see the synthetic entry ``mock_permissive_entry`` appear
under "Permissively-licensed models" in addition to SFace + dlib +
buffalo_s. The server.log line for the fetch will read
``Remote registry fetch failed: ... (using bundled baseline)`` only
if the verifier rejects the signature — confirm by running
``bpp model registry verify`` against the mock URL output.

Stop the mock with Ctrl-C.

What this validates

* The Batch 8 + Batch 10 fetch path works end-to-end against a real
  signed manifest (not just against unit-test fixtures).
* The verifier accepts manifests signed with the production primary
  key and rejects manifests signed with anything else.
* The overlay merger applies the new entry without touching the
  bundled SFace / dlib / buffalo_s baseline.
* The insecure-mode env vars work as documented.

What this does NOT validate

* HTTPS / TLS plumbing (we're plain http:// to localhost).
* The arkalogy.github.io URL resolution (we're overriding it).
* Production-grade redirect handling on the real host.

For the production check (after publishing the real Pages repo),
run::

    curl -fsSL https://arkalogy.github.io/bppicker-registry/registry.json \\
      | .venv/bin/bpp model registry verify /dev/stdin
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import socketserver
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Ensure project root is on path so this works when invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bpp.registry import sign_manifest

MOCK_BIND_HOST = "127.0.0.1"
MOCK_PORT = 9088


def _load_primary_key() -> tuple[str, Ed25519PrivateKey]:
    """Load the Arkalogy primary signing key from
    ``~/.config/bpp/signing-keys/``."""
    key_path = (
        Path.home() / ".config" / "bpp" / "signing-keys" / "arkalogy-primary-2026-06.private.key"
    )
    if not key_path.exists():
        sys.exit(
            f"primary signing key not found at {key_path} — generate "
            "one per docs/key-rotation.md before running the mock"
        )
    raw = key_path.read_text(encoding="ascii").strip()
    key_bytes = base64.b64decode(raw)
    return "arkalogy-primary-2026-06", Ed25519PrivateKey.from_private_bytes(key_bytes)


def _build_mock_manifest() -> dict:
    """A manifest carrying one synthetic entry the registry doesn't
    already ship. Visible in ``bpp model list`` after the overlay
    applies."""
    return {
        "schema_version": 1,
        "generated_at": "2026-06-03T18:00:00+00:00",
        "entries": [
            {
                "id": "mock_permissive_entry",
                "display_name": "Mock permissive (overlay smoke-test)",
                "kind": "face_embedder",
                "source_url": "https://example.invalid/mock.onnx",
                "terms_url": "https://example.invalid/LICENSE",
                "terms_permalink_url": None,
                "terms_retrieved_at": "2026-06-03",
                "license_summary": (
                    "Synthetic entry served by run_mock_registry.py for "
                    "end-to-end verification of the Batch 8 + 10 fetch + "
                    "verify + apply chain. Not a real model."
                ),
                "requires_explicit_ack": False,
                "ack_text_version": "canonical-disclaimer-v1",
                "ack_text_sha256": "0" * 64,
                "upstream_claimed_license_class": "apache_2_0",
                "commercial_use_restriction_known": False,
                "bppicker_commercial_default_allowed": True,
                "commercial_unlock_requires_rights_assertion": False,
                "status": "available",
                "training_data": "synthetic / mock",
                "weight_sha256": "0" * 64,
                "default_for_kind": False,
                "ack_text_kind": "canonical",
            }
        ],
    }


def _make_handler_for(payload: bytes):
    """Return an http.server handler class that serves ``payload`` at
    ``/registry.json`` and 404s everything else."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/registry.json":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found\n")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: object) -> None:
            # Silence the default per-request stderr log; replace with
            # a single concise line so the mock console isn't noisy.
            sys.stderr.write(f"  [mock-registry] {self.address_string()} {fmt % args}\n")

    return _Handler


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local mock remote-registry server.",
    )
    parser.add_argument("--port", type=int, default=MOCK_PORT, help="Bind port")
    parser.add_argument(
        "--out",
        help="Optional: also write the signed manifest to this path",
    )
    args = parser.parse_args()

    key_id, priv = _load_primary_key()
    manifest = _build_mock_manifest()
    signed = sign_manifest(manifest, [(key_id, priv)])
    payload = json.dumps(signed, indent=2, sort_keys=True).encode("utf-8")

    if args.out:
        Path(args.out).expanduser().write_bytes(payload)
        sys.stderr.write(f"wrote signed manifest to {args.out}\n")
    else:
        tmp = Path(tempfile.gettempdir()) / "bpp-mock-registry.json"
        tmp.write_bytes(payload)
        sys.stderr.write(f"signed manifest also at {tmp}\n")

    handler = _make_handler_for(payload)
    with socketserver.TCPServer((MOCK_BIND_HOST, args.port), handler) as httpd:
        url = f"http://{MOCK_BIND_HOST}:{args.port}/registry.json"
        sys.stderr.write(f"\nServing {len(payload)} bytes at {url}\n")
        sys.stderr.write(
            "Signed by:        " + key_id + "\n"
            "Synthetic entry:  mock_permissive_entry\n\n"
            "To exercise the overlay (in another terminal):\n"
            f"  export BPP_REMOTE_REGISTRY_URL={url}\n"
            "  export BPP_REMOTE_REGISTRY_INSECURE=1\n"
            "  .venv/bin/bpp model list\n\n"
            "Stop with Ctrl-C.\n\n"
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            sys.stderr.write("\nstopping mock registry\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
