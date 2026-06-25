# Go-live runbook — cutting a public release

Operational, copy-pasteable sequence for shipping a release to PyPI. This is
the **code-release** companion to [`release-checklist.md`](release-checklist.md)
(which covers the one-time legal/infra prereqs: signed registry Pages, branch
protection, signing-key cold storage). Do those first; this runbook assumes
they're done.

Branch model: feature → `develop` → `main`. CI runs on PRs to `main`. PyPI
package name is **`bppicker`**; `main` is the release branch.

> **Done means:** a first-time user on a clean machine can install and run
> v0.1.0 — `pip install bppicker` works *or* the signed/notarized DMG
> downloads from the public Pages page and launches — which requires the
> repo public, `bppicker` 0.1.0 live on PyPI, the notarized DMG reachable,
> and the post-publish smoke (§3) green. Anything short of that is not done.

---

## 0. Pre-flight (on `develop`, ~5 min)

Confirm the release is actually green before touching `main`. Run locally:

```bash
.venv/bin/ruff check && .venv/bin/ruff format --check .
npm run lint && npm run typecheck && npm run format:check && npm run test:js
.venv/bin/pytest -q
scripts/run_e2e.sh ci          # synthetic + empty Playwright passes
.venv/bin/python -m build      # sdist + wheel build smoke
```

Version + changelog sync (**all four** must agree on the version you're
cutting — the desktop build hard-fails if `tauri.conf.json` disagrees):

```bash
grep -m1 'version' pyproject.toml          # version = "0.1.0"
grep '__version__' bpp/__init__.py         # __version__ = "0.1.0"
python3 -c "import json; print(json.load(open('desktop/src-tauri/tauri.conf.json'))['version'])"  # 0.1.0
grep -m1 '## \[' CHANGELOG.md              # top entry == [0.1.0]
```

If you're cutting a **new** version (e.g. 0.2.0): bump `pyproject.toml`,
`bpp/__init__.py`, **and `desktop/src-tauri/tauri.conf.json`**, move
CHANGELOG `[Unreleased]` → `[0.2.0]`, commit to `develop`, re-run
pre-flight.

**One-time prereqs for the signed desktop app** (do once, well before the
first release — see [`macos-signing.md`](macos-signing.md)): add the six
`APPLE_*` repo secrets and smoke-test the notarized DMG via the
"Desktop App (macOS)" workflow's `workflow_dispatch` path. Also enable
GitHub Pages (Settings → Pages → Source = "GitHub Actions") so the
download page publishes. Without the secrets the release still produces
an *unsigned* DMG (Gatekeeper warning); without Pages there's no
non-technical download link.

`develop` must be pushed and clean: `git status` → clean; `git log
origin/develop..develop` → empty.

---

## 1. Promote `develop` → `main`

```bash
gh pr create --base main --head develop \
  --title "Release v0.1.0" \
  --body-file /tmp/release-pr-body.md     # see §4 for body, or write inline
```

- CI runs on the PR. It will be **green** (a previously-red `main`-targeting
  dependabot PR was red only because `main` lagged `develop`'s format/lint
  fixes — this merge carries them).
- Requires code-owner approval (branch protection). Self-approve or have the
  second maintainer review the `bpp/registry/` paths.
- **Merge** (merge commit, not squash — preserves the develop history).

This merge alone also clears the open **Dependabot security alerts** and the
red dependabot **CI PRs** on `main` — they were all "main is behind develop."

---

## 2. Publish to PyPI (tag + GitHub Release)

`publish.yml` triggers on a **published GitHub Release**, verifies the tag
matches the package version, builds, and uploads via **trusted publishing**
(OIDC — no API token to manage). The PyPI side must already be configured:
pypi.org → project `bppicker` → Publishing → trusted publisher = repo
`Arkalogy/best-photo-picker`, workflow `publish.yml`, environment `pypi`.

Cut the release from `main`:

```bash
git checkout main && git pull origin main
gh release create v0.1.0 \
  --target main \
  --title "bppicker v0.1.0" \
  --notes-file /tmp/release-notes.md     # see §4
```

(Optional dry run first: Actions → "Publish to PyPI" → Run workflow →
`publish=false` builds the artifacts WITHOUT uploading, to confirm the build
+ tag-match step before the real release.)

Watch it:

```bash
gh run watch $(gh run list --workflow=publish.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```

Green → the wheel + sdist are live on https://pypi.org/project/bppicker/.

The **same** `release: published` event also fires `release-desktop.yml`,
which builds the signed + notarized macOS DMG and attaches
`BestPhotoPicker-macOS-arm64.dmg` to the Release (~25 min on a 10×-billed
macOS runner). Watch it the same way (`--workflow=release-desktop.yml`).
Confirm the Release page shows the DMG asset, then verify it's notarized
by downloading it on a Mac and running
`spctl -a -t open -vv BestPhotoPicker-macOS-arm64.dmg` (want
"source=Notarized Developer ID"). The download page links to this asset
via the stable `releases/latest/download/` URL.

---

## 3. Post-publish smoke test (clean machine / fresh venv, ~3 min)

Prove a brand-new user's install path works — do NOT test from the repo venv:

```bash
pipx install bppicker            # or: pip install bppicker in a fresh venv
bpp --help                       # subcommands render, no traceback
pip install "bppicker[web]" && bpp demo   # generates a sample lib, opens the UI
```

The `bpp demo` path is the 30-second eval in the README; if it works end to
end on a clean box, the release is good.

---

## 4. Release-notes / PR body

Generate from the commit range since the last tag:

```bash
git log v0.1.0..HEAD --oneline        # (first release: git log --oneline)
```

Group by prefix (feat / fix / perf / docs) into the notes. The CHANGELOG
`[0.1.0]` section is the canonical source — paste/adapt it.

---

## 5. Post-launch verification

- Dependabot: repo → Security → Dependabot alerts → **0 open** (the vite +
  audit-fixes landed on `main`).
- Remote registry: a fresh `bpp serve` logs no `registry.json 404` warning
  (Pages is serving the signed baseline — release-checklist §1).
- PyPI page renders the README + metadata correctly.
- Tag `v0.1.0` shows under Releases with the notes.

---

## 6. Rollback

PyPI versions are **immutable** — you can't overwrite `0.1.0`, only yank or
supersede.

- **Bad build / critical bug found post-publish:** `yank` the release so new
  installs skip it (existing pins still resolve):
  pypi.org → bppicker → Manage → Releases → 0.1.0 → Yank. Then fix on
  `develop`, cut `0.1.1` (this runbook from §0).
- **Bad merge to `main` (pre-tag):** `git revert -m 1 <merge-sha>` on `main`
  via a PR; `develop` is untouched.
- Never delete a published tag/version and re-push the same number — it breaks
  anyone who already pinned it.
