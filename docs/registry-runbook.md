# Registry runbook — publishing manifest updates

The signed registry at `https://arkalogy.github.io/bppicker-registry/registry.json` is bpp's takedown / status-change channel. Updating it is a maintainer-only operation.

## When to publish an update

Any time the bundled baseline in the shipped wheel isn't current. Typical triggers:

| Trigger | Action |
|---|---|
| Upstream model author requests takedown | Flip `status` → `legally_blocked` |
| Upstream license changes (e.g. AGPL → commercial-only) | Update `license_summary` + `requires_explicit_ack` |
| Upstream deprecates a model | Flip `status` → `withdrawn_no_new_downloads` |
| New restricted entry to add | Add to `entries` list |
| New permissive entry to add | Add to `entries` list |

## Prerequisites (one-time, already done)

- `Arkalogy/bppicker-registry` repo exists, GitHub Pages on, served at `https://arkalogy.github.io/bppicker-registry/`.
- Primary signing private key held in a secure vault that encrypts at rest and in transit (password manager, hardware token, KMS, etc.). The key must never be persisted to disk in plaintext on the maintainer's workstation; it is pasted into the signing script and held only in memory.
- Public key trusted in `bpp/registry/trusted_keys.py` slot 0.

## Publishing a change

1. **Author the entries list** in `entries.json`. Use the bundled baseline (`bpp/registry/builtins.py`) as the schema reference. Example for a single takedown:

   ```json
   [
     {
       "id": "ultralytics_yolov11n_pets",
       "display_name": "Ultralytics YOLOv11n (pet detection, AGPL-3.0)",
       "kind": "pet_detector",
       "status": "legally_blocked",
       "license_summary": "...",
       "requires_explicit_ack": true,
       "commercial_use_restriction_known": true,
       "ack_text_kind": "canonical",
       "ack_text_version": "canonical-disclaimer-v2",
       "ack_text_sha256": "<see disclaimers.py>",
       "terms_url": "...",
       "terms_permalink_url": "..."
     }
   ]
   ```

2. **Build + sign**:

   ```bash
   cd /path/to/bpp
   .venv/bin/python scripts/publish_registry_manifest.py --entries entries.json
   ```

   Retrieve the private key from its secure vault and paste it when prompted. The script verifies the pasted key derives to the trusted public key before signing — a wrong-key paste fails fast. Output: `/tmp/registry.json`.

3. **Verify locally before publishing**:

   ```bash
   .venv/bin/python -c "
   import json
   from bpp.registry.signed_manifest import verify_manifest
   from bpp.registry.trusted_keys import trusted_key_list
   r = verify_manifest(json.load(open('/tmp/registry.json')),
                       trusted_keys=trusted_key_list())
   assert r.is_valid, r.errors
   print('OK', len(r.entries), 'entries,', r.valid_signatures)
   "
   ```

4. **Publish**:

   ```bash
   gh repo clone Arkalogy/bppicker-registry /tmp/bppicker-registry
   cp /tmp/registry.json /tmp/bppicker-registry/registry.json
   cd /tmp/bppicker-registry
   git commit -am "Takedown: ultralytics_yolov11n_pets → legally_blocked"
   git push
   rm -rf /tmp/bppicker-registry /tmp/registry.json
   ```

5. **Wait ~30 s** for GitHub Pages to rebuild, then confirm the live URL:

   ```bash
   curl -fsS https://arkalogy.github.io/bppicker-registry/registry.json | jq .generated_at
   ```

6. **Restart a test bpp instance** and confirm no 404 warning in the log, and the new status is reflected in Settings → Models for the affected entry.

## Dual-signature changes (restriction loosening)

Currently impossible — only one trusted key exists. Loosening requires:

1. Generate a second keypair (`scripts/publish_registry_manifest.py` can be adapted, or generate via the `cryptography` library directly).
2. Add the public key as slot 1 in `trusted_keys.py`. Ship a new bpp wheel.
3. Escrow the secondary private key separately (cold storage, co-signer device, hardware token).
4. Use `scripts/sign_registry_manifest.py --key primary.key --key secondary.key` for dual-sig manifests.

Until then, loosening can only be done by shipping a new bpp wheel that updates the bundled baseline.

## Key compromise — incident response

If the primary key is suspected compromised:

1. Generate a fresh primary keypair.
2. Replace the public key in `bpp/registry/trusted_keys.py` slot 0.
3. Ship an emergency bpp release. Until users upgrade, they continue trusting the old (compromised) key.
4. Store the new private key in the maintainer's secure vault; revoke / destroy any copies of the old one.
5. Publish a fresh manifest signed with the new key. The old manifest stays live until overwritten; it still verifies under the new wheel as long as the old public key is included in slot N during the cut-over window.
6. After the cut-over (typically one bpp release later), remove the old public key from `trusted_keys.py`.

## What's NOT in this runbook

- **Wiring a new model into bpp's code** (which load path, which files to edit so it gates + shows in Settings → Models) — see [adding-a-model.md](adding-a-model.md). This runbook is only the signed-manifest publishing side.
- **First-time setup** (creating the registry repo, generating the initial keypair, enabling Pages) — that's a one-time activity covered in `docs/release-checklist.md`. Done already.
- **Branch protection / CODEOWNERS** — repo-level GitHub config, not a recurring task.
