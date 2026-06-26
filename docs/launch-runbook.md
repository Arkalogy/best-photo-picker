# Launch runbook — everything left before v0.1.0 is public

This is the single ordered list of what still stands between today and a
published v0.1.0. The code is done; what remains is **maintainer-only**
setup (Apple account, repo admin, PyPI config, key handling) plus the
mechanical release cut. Claude can't do any of these — they need your
accounts and admin rights.

Order matters: the **flip to public** (Phase 2) unlocks branch
protection, and signing/PyPI setup (Phase 1) must be done before the cut
(Phase 3) or the release ships unsigned / fails to publish.

For the mechanical cut itself (gates → PR → tag → publish → smoke),
Phase 3 hands off to [`go-live-runbook.md`](go-live-runbook.md). Legal
detail lives in [`release-checklist.md`](release-checklist.md); signing
detail in [`macos-signing.md`](macos-signing.md).

---

## Status at a glance (verified 2026-06-18)

| Item | State |
|---|---|
| Remote registry Pages serving a signed manifest | ✅ done (serving) |
| buffalo_s inference + acceptance chain | ✅ done |
| `.github/CODEOWNERS` covering sensitive paths | ✅ in repo |
| Signed/notarized DMG workflow | ✅ code shipped — needs secrets (1.1) |
| Download page + Pages deploy workflow | ✅ code shipped — needs Pages on (2.3) |
| go-live cut runbook | ✅ written |
| macOS signing secrets + DMG smoke test | ⬜ Phase 1 |
| PyPI trusted publisher configured | ⬜ Phase 1 |
| Secondary signing key in cold storage | ⬜ Phase 1 |
| buffalo_s browser click-through | ⬜ Phase 1 |
| Repo flipped to public | ⬜ Phase 2 (the gate) |
| Branch protection + code-owner review on main | ⬜ Phase 2 |
| GitHub Pages enabled (Actions source) | ⬜ Phase 2 |
| Repo presentation (desc, topics, social image) | ⬜ Phase 2 |
| The release cut | ⬜ Phase 3 |

---

## Phase 1 — One-time setup (do now; order-independent, repo can stay private)

### 1.1 macOS signing secrets + credential validation — ✅ DONE (2026-06-18)
Per [`macos-signing.md`](macos-signing.md): Developer ID Application cert
created, exported as `.p12`, and all six `APPLE_*` secrets set on the repo.
Validated **locally** (the CI `workflow_dispatch` smoke path isn't available
until the workflow reaches `main` — GitHub only dispatches workflows on the
default branch):
- `security find-identity -v -p codesigning` → cert + private key valid.
- `xcrun notarytool history --apple-id … --password … --team-id …` →
  authenticated ("No submission history"), proving the Apple-ID / app-
  specific-password / Team-ID trio is correct.

Both failure-prone pieces (cert + notarization auth) are proven. The full
signed-DMG CI build runs for the first time on the real release publish.

### 1.2 Configure the PyPI trusted publisher
The package name `bppicker` has no releases yet; `publish.yml` uploads via
trusted publishing (OIDC, no token). Configure the **pending publisher**
so the first release creates the project automatically:
- pypi.org → Your projects → Publishing → "Add a pending publisher":
  - PyPI project name: `bppicker`
  - Owner: `Arkalogy`  ·  Repo: `best-photo-picker`
  - Workflow: `publish.yml`  ·  Environment: `pypi`
- **Done when** the pending publisher row appears under your PyPI account.
- (Optional dry run: Actions → "Publish to PyPI" → Run workflow →
  `publish=false` builds the artifacts without uploading.)

### 1.3 Move the secondary signing key to cold storage
Per [`release-checklist.md`](release-checklist.md) §3 and
[`key-rotation.md`](key-rotation.md): back up
`~/.config/bpp/signing-keys/arkalogy-secondary-2026-06.private.key`
off-machine (safe / `age`-encrypted blob / USB in a separate location),
then **delete the plaintext from the working machine**. The primary key
stays (0600). The dual-signature requirement is only load-bearing if the
secondary is genuinely offline.
- **Done when** the plaintext secondary file no longer exists locally and
  the backup is verified readable.

### 1.4 Drive the buffalo_s click-through in a real browser
Per [`release-checklist.md`](release-checklist.md) §4. Start the demo
server, open Settings → Models → buffalo_s, exercise the acceptance
dialog (all-four-boxes gate, the commercial-context hard-block + escape
hatch). Fix any wording/sizing/escape-route issue you hit.
- **Done when** the dialog renders correctly and both the accept and
  reject paths behave.

---

## Phase 2 — Go public and lock it down (the gate)

> Everything here is gated on the repo being public. Branch protection is
> not available on a private free-tier repo (confirmed: the API returns
> "make this repository public or upgrade to Pro").

### 2.1 Final pre-public git-history scan
Going public exposes **all git history**, not just the current tree. The
release audit's legal/community pass was clean, but re-confirm nothing
sensitive is in history before the irreversible flip:
```bash
git log --all -p | grep -iE '(password|api[_-]?key|secret|BEGIN .*PRIVATE KEY|/Users/[a-z]+/)' | head
```
Expect no real secrets. Personal `/Users/...` paths in old commits are
cosmetic but worth a glance.
- **Done when** the scan shows nothing that can't be public.

### 2.2 Flip the repo to public
GitHub → Settings → General → Danger Zone → Change visibility → Public.
- **Done when** `gh repo view Arkalogy/best-photo-picker --json visibility`
  reports `PUBLIC`.

### 2.3 Enable GitHub Pages for the download page
Settings → Pages → Build and deployment → Source = **GitHub Actions**.
Then trigger the deploy (Actions → "Download page (GitHub Pages)" → Run
workflow, or it runs on the next push to `main` touching `docs/landing/`).
- **Done when** `https://arkalogy.github.io/best-photo-picker/` loads the
  download page. (Point arkalogy.com at it via CNAME later if you want a
  branded URL.)

### 2.4 Enable branch protection + code-owner review
Per [`release-checklist.md`](release-checklist.md) §2 — now unlocked by
2.2. On `main`: require PR, require approvals, **require Code Owner
review**, require status checks (`ci.yml` + JS gates + `test:e2e:list`),
restrict who can push.
- **Done when** a draft PR touching `bpp/registry/builtins.py` shows
  "Code owner review required."

### 2.5 Repo presentation
The repo is the portfolio piece. Set: description, topics/tags, social
preview image (Settings → General), and the website link (arkalogy.com or
the Pages URL). Optionally pin a Discussion or the README.
- **Done when** the repo header shows a description, topics, and a link.

---

## Phase 3 — Cut the release

Everything above is the prerequisite. The mechanical sequence — pre-flight
gates, `develop`→`main` PR, merge, tag + GitHub Release (which fires both
the PyPI publish **and** the signed-DMG build on the same event),
clean-machine smoke test, post-launch verification, rollback — is in
[`go-live-runbook.md`](go-live-runbook.md). Run it top to bottom.

A release is "done" when:
- pypi.org/project/bppicker shows 0.1.0,
- the GitHub Release has the `BestPhotoPicker-macOS-arm64.dmg` asset and it
  reports Notarized,
- a fresh `pipx install "bppicker[web]"` + `bpp demo` works on a clean
  machine,
- the download page downloads and opens the DMG with no Gatekeeper warning,
- Dependabot shows 0 open alerts.
