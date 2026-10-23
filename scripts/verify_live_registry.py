#!/usr/bin/env python3
"""Release gate: the published model-registry manifest must verify with
the verifier this build ships.

The remote manifest is how a model can be withdrawn after release (a
takedown, a licence change). A manifest that fails verification is
ignored by every installed copy, with a warning on every start, and the
takedown channel is inert — exactly what happened when the
``manifest_version`` field arrived in the verifier before the published
manifest carried it. Run this before tagging, and the release
workflows run it on every published release.

Exit status 0 when the live manifest verifies, 1 otherwise. Honours
``BPP_REMOTE_REGISTRY_URL`` like the app does.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    # The import-time fetch would only log; this script reports.
    os.environ.setdefault("BPP_DISABLE_REMOTE_REGISTRY", "1")
    from bpp.registry.remote_registry import _resolve_effective_url, fetch_remote_manifest
    from bpp.registry.signed_manifest import verify_manifest
    from bpp.registry.trusted_keys import is_all_placeholder_keys, trusted_key_list
    from bpp.utils.json_utils import safe_json_loads

    url = _resolve_effective_url()
    keys = trusted_key_list()
    if is_all_placeholder_keys(keys):
        print("FAIL: this checkout carries placeholder trusted keys; nothing could verify")
        return 1
    data = fetch_remote_manifest(url)
    if data is None:
        print(f"FAIL: could not fetch {url}")
        return 1
    manifest = safe_json_loads(data, expected_type=dict, context="live registry manifest")
    if manifest is None:
        print(f"FAIL: {url} is not a JSON object")
        return 1
    result = verify_manifest(manifest, trusted_keys=keys)
    if not result.is_valid:
        print(f"FAIL: {url} does not verify with this build's verifier:")
        for err in result.errors:
            print(f"  - {err}")
        print("Republish the manifest (scripts/publish_registry_manifest.py) before releasing.")
        return 1
    print(
        f"OK: {url} verifies (manifest_version {result.manifest_version}, "
        f"{len(result.entries)} entries, {len(result.valid_signatures)} valid signature(s))"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
