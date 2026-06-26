# Signing-key rotation procedure

This doc covers the operational side of the Batch 8 / item 23 signing chain: where the private keys live, how to sign a new registry manifest, and how to rotate a key.

## Where the keys live

The trusted **public** keys ship in the BPP source tree at `bpp/registry/trusted_keys.py`. A BPP release is the only way to change the trust set on a user's machine; the file is intentionally append-only across releases (see [Rotation cadence](#rotation-cadence) below).

The matching **private** keys live OUTSIDE the repo, at:

```
~/.config/bpp/signing-keys/arkalogy-primary-2026-06.private.key
~/.config/bpp/signing-keys/arkalogy-secondary-2026-06.private.key
```

Mode `0600`. Base64-encoded raw 32-byte Ed25519 private keys.

The **primary** key signs every routine manifest update — new permissive entries, ack-text version bumps, status tightenings (`available → withdrawn_no_new_downloads → legally_blocked`). It lives on the maintainer's primary working machine.

The **secondary** key is the dual-signature partner. It signs ONLY restriction-class relaxations and new restricted-entry additions, where the overlay merger demands `valid_signature_count == 2`. It **must** live in physically separate storage — a hardware token, a USB stick in a drawer, a sealed envelope. If the secondary lives on the working machine, a single workstation compromise gives an attacker both signing keys and the dual-sig requirement collapses into single-sig — the cold-storage isolation is the load-bearing protection, not a recommendation. Pulling the secondary out of cold storage is itself a one-per-quarter event; if the secondary is online more often than that, the dual-sig requirement degrades into a routine motion.

## Signing a manifest

Use the bundled helper:

```bash
.venv/bin/python scripts/sign_registry_manifest.py \
    --in  /path/to/unsigned-manifest.json \
    --out /path/to/signed-manifest.json \
    --key ~/.config/bpp/signing-keys/arkalogy-primary-2026-06.private.key
```

To dual-sign (restriction relaxation or new restricted entry), pass `--key` twice:

```bash
.venv/bin/python scripts/sign_registry_manifest.py \
    --in  /path/to/unsigned-manifest.json \
    --out /path/to/signed-manifest.json \
    --key ~/.config/bpp/signing-keys/arkalogy-primary-2026-06.private.key \
    --key ~/.config/bpp/signing-keys/arkalogy-secondary-2026-06.private.key
```

Then verify locally before publishing:

```bash
.venv/bin/bpp model registry verify /path/to/signed-manifest.json
```

`OK: manifest at <path> verifies against ...` means the manifest is publishable. `FAIL:` means something is wrong; do NOT publish.

## Rotating a key (planned)

Calendar-rotate the primary key every 12 months even without a suspected compromise. The cut-over is a release event:

1. Generate a new keypair (see [Generating a new keypair](#generating-a-new-keypair)).
2. **Append** the new `TrustedKey` to the `TRUSTED_KEYS` tuple in `bpp/registry/trusted_keys.py`. Do NOT remove the old entry yet — manifests still in flight may be signed under the old key.
3. Ship a BPP release with the appended trust set.
4. Sign all NEW manifests with the new private key.
5. In the FOLLOWING release (one release after the rotation release), remove the old entry from `TRUSTED_KEYS`. By this point any manifest still signed under the old key is stale and should be re-signed anyway.

This cut-over preserves verifier compatibility during the window when users are still on the previous BPP version.

## Rotating a key (compromise)

If you suspect the primary private key has been exfiltrated:

1. Generate a new primary keypair IMMEDIATELY.
2. In a hotfix release: REPLACE (do not append) the old primary entry with the new one in `TRUSTED_KEYS`. Old manifests stop verifying as soon as the user updates.
3. Re-sign and re-publish every currently-published manifest under the new key.
4. Optionally: publish a `legally_blocked` status flip for every entry that was reachable under the compromised key, signed by the unaffected secondary. This nukes downloads for the compromise window.
5. Investigate the compromise vector. The maintainer's primary working machine is the typical surface; secrets management or laptop theft are the usual stories.

If the secondary is compromised, the procedure is the same with primary/secondary swapped. The secondary's job is harder to compromise (cold storage), so a confirmed secondary leak is a serious signal.

## Generating a new keypair

The same script that generated the original 2026-06 pair:

```python
import base64, os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

slug = "arkalogy-primary-2027-06"  # bump rotation epoch
priv = Ed25519PrivateKey.generate()
pub = priv.public_key().public_bytes_raw()
priv_bytes = priv.private_bytes_raw()
out = Path.home() / ".config" / "bpp" / "signing-keys" / f"{slug}.private.key"
out.write_text(base64.b64encode(priv_bytes).decode("ascii") + "\n")
os.chmod(out, 0o600)
print("public:", base64.b64encode(pub).decode("ascii"))
```

Paste the printed public-key string into `TRUSTED_KEYS` in the source.

## Rotation cadence

| Situation | Action |
|---|---|
| 12 months since the primary was generated | Planned rotation per above |
| 24 months since the secondary was generated | Planned rotation per above (less frequent because cold-stored) |
| Confirmed leak (primary or secondary) | Compromise rotation per above, hotfix release |
| Maintainer leaves the project | Both keys rotate per compromise procedure |
| BPP repo transferred to a new org | Both keys rotate as a planned cutover |

## Why not store the keys in a secrets manager

We considered AWS Secrets Manager / 1Password CLI / age-encrypted files. For a solo-maintainer OSS project, the operational simplicity of a `0600` file the maintainer can reason about beats the dependency tree of a hosted secrets manager. The dual-sig requirement is the load-bearing protection here, not the storage backend — even if the primary is found in plaintext on a stolen laptop, the secondary's cold-storage isolation prevents the attacker from forging a restriction-relaxation manifest.

If/when the project grows beyond a single maintainer, revisit. The trust set is one append in `trusted_keys.py` away from supporting N maintainers' individual keys.
