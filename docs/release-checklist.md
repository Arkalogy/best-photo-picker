# Pre-release checklist — legal posture

The 10-batch legal-posture rollout shipped the infrastructure. The items below are the external maintainer actions that complete the public-launch readiness pass.

## 1. Stand up the remote-registry GitHub Pages repo

The bppicker remote-registry overlay fetches a signed manifest from a hardcoded URL:

```
https://arkalogy.github.io/bppicker-registry/registry.json
```

This is the Item 12 takedown channel: when an upstream model has to be flipped to `legally_blocked` or `withdrawn_no_new_downloads`, a maintainer publishes a re-signed manifest here, and every running bppicker installation picks it up on next startup. Until this URL serves a valid signed manifest, every bppicker startup logs a 404 warning.

### Steps

1. Create a new public GitHub repo: `Arkalogy/bppicker-registry`.
2. In the repo Settings → Pages, enable Pages on the `main` branch root.
3. Generate an empty signed baseline manifest:

   ```bash
   cat > /tmp/empty-baseline.json <<'EOF'
   {
     "schema_version": 1,
     "generated_at": "<today's ISO timestamp>",
     "entries": []
   }
   EOF

   .venv/bin/python scripts/sign_registry_manifest.py \
     --in /tmp/empty-baseline.json \
     --out registry.json \
     --key ~/.config/bpp/signing-keys/arkalogy-primary-2026-06.private.key
   ```

4. Commit `registry.json` to the new repo. Verify it's reachable:

   ```bash
   curl -fsSL https://arkalogy.github.io/bppicker-registry/registry.json | \
     .venv/bin/bpp model registry verify /dev/stdin
   ```

   Should print `OK: manifest at /dev/stdin verifies against 1 bundled key(s)`.

5. Once published, bppicker startups stop emitting the 404 warning.

### What goes in `entries`

The bundled baseline (SFace, dlib, buffalo_s) is shipped in source via `bpp/registry/builtins.py` — the remote registry overlays *changes* to that baseline, it doesn't restate it. Typical contents:

- A new permissive entry the maintainer wants to ship between BPP releases
- A status flip on an existing entry (`available → withdrawn_no_new_downloads`)
- An ack-text version bump

For relaxations (restriction class going DOWN), sign with BOTH keys.

## 2. Enable GitHub branch protection with code-owner review

The MODEL_POLICY.md + .github/CODEOWNERS combination gates legally-sensitive paths (`bpp/registry/`, `bpp/utils/download.py`, the policy file itself), but **only** when GitHub branch protection is configured to enforce code-owner review.

### Steps

In the `Arkalogy/best-photo-picker` repo:

1. Settings → Branches → Add branch protection rule for `main`.
2. Check **Require a pull request before merging**.
3. Check **Require approvals** with at least one approval.
4. Check **Require review from Code Owners**.
5. Check **Require status checks to pass before merging** and add the CI gates: `ci.yml` (Python tests), the JS gates (lint, typecheck, test:js), and `test:e2e:list`.
6. Check **Restrict who can push to matching branches** and limit to the maintainer team.

Repeat for `develop` (CI optional, but code-owner review on for the same reasons).

7. Verify by opening a draft PR that touches `bpp/registry/builtins.py` — GitHub should mark it "Code owner review required."

## 3. Move the secondary signing key to cold storage

The primary key at `~/.config/bpp/signing-keys/arkalogy-primary-2026-06.private.key` stays on the working machine (with `0600` perms).

The secondary at `~/.config/bpp/signing-keys/arkalogy-secondary-2026-06.private.key` **must** leave the machine — the cold-storage isolation is what makes the dual-signature requirement load-bearing (see [`docs/key-rotation.md`](key-rotation.md) for the threat model). Options:

- Write the base64 string to a sticky note, put it in a safe / sealed envelope, delete the file.
- Encrypt with `age` using a passphrase only you know, store the encrypted blob in a separate location.
- Burn to a USB stick, store in physically separate location (different building if possible).

Whichever you pick, **delete the plaintext file from the working machine** after backup. Pulling the secondary out of cold storage is a once-per-quarter event at most — typical signing only ever uses the primary.

## 4. Drive the click-through end-to-end in a real browser

The CLI walkthrough confirms the data layer works. The browser walkthrough confirms the UI layer does too — that the dialog renders, the checkboxes are clickable, the disclaimer text is readable, and the rejection path looks reasonable. The buffalo_s entry is in the bundled baseline now, so:

1. `.venv/bin/bpp serve --library ~/Pictures/BestPhotoPickerDemo --no-browser`
2. Open `http://127.0.0.1:5001/`
3. Settings → Models → look for buffalo_s in the picker
4. Click it; the canonical-disclaimer dialog should appear
5. Try to dismiss without checking all boxes → "must acknowledge" error
6. Check all four boxes, hit Accept → acceptance row in `bpp model accepted`
7. Settings → Use Context → Commercial → re-attempt to load buffalo_s → hard-block dialog with the "I have separate commercial rights" escape hatch

Items that surface at this step are typically: wording feels off in context, the dialog is too small / too big, an escape route doesn't work as expected. Fix what you find.

## 5. buffalo_s inference path — DONE

This list is about the **legal** posture — the metadata + acceptance + signing chain. Getting buffalo_s to PRODUCE embeddings was a separate scope, and it has since landed:

- InsightFace ONNX extractor in `bpp/scoring/face_embed_buffalo_s.py`
- Wired into the embedder dispatch in `bpp/scoring/face_embed.py` (`_extract_buffalo_s`, ~line 441), gated at load by `enforce_load_policy_for("insightface_buffalo_s")`
- Falls back to SFace only when onnxruntime is unavailable

Selecting buffalo_s in the picker now records the acceptance row AND produces embeddings on the next analyze. No outstanding work here.
