# macOS code signing & notarization (maintainer setup)

One-time setup so `release-desktop.yml` produces a **signed + notarized
DMG** that opens on double-click — no Gatekeeper "Open Anyway" detour.
You need an active **Apple Developer Program** membership. After this is
done, every published release builds the DMG automatically; you never run
`codesign` or `notarytool` by hand (Tauri does it from the env vars).

## 1. Get the Developer ID Application certificate

In **Xcode → Settings → Accounts → Manage Certificates → +** create a
**Developer ID Application** certificate (or, on the Apple Developer
portal, Certificates → + → Developer ID Application). It lands in your
login Keychain.

## 2. Export it as a `.p12`

In **Keychain Access**, find *"Developer ID Application: <NAME> (<TEAMID>)"*,
right-click → **Export** → `.p12`. Set an export password — you'll need it
in step 4 as `APPLE_CERTIFICATE_PASSWORD`.

Base64-encode the `.p12` for the GitHub secret (one line, no newlines):

```bash
base64 -i Certificates.p12 | pbcopy   # now on your clipboard
```

## 3. Create an app-specific password (for notarization)

Notarization authenticates as your Apple ID, but you must NOT use your
real account password in CI. At **appleid.apple.com → Sign-In and
Security → App-Specific Passwords**, generate one (label it e.g.
"bpp notarization"). That value is `APPLE_PASSWORD`.

Find your **Team ID** at **developer.apple.com → Membership** (10 chars,
e.g. `A1B2C3D4E5`). That's `APPLE_TEAM_ID`.

## 4. Add the six repo secrets

**Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `APPLE_CERTIFICATE` | the base64 `.p12` blob from step 2 |
| `APPLE_CERTIFICATE_PASSWORD` | the `.p12` export password from step 2 |
| `APPLE_SIGNING_IDENTITY` | `Developer ID Application: <NAME> (<TEAMID>)` — exact Keychain name |
| `APPLE_ID` | your Apple account email |
| `APPLE_PASSWORD` | the app-specific password from step 3 |
| `APPLE_TEAM_ID` | the 10-char Team ID from step 3 |

## 5. Validate the credentials BEFORE the first release

Notarization usually takes 2–3 tries to get right (wrong identity string,
missing app-specific password, etc.), so prove the chain works before a
real publish.

**Note on the CI smoke path:** GitHub only allows manual `workflow_dispatch`
runs of a workflow that exists on the **default branch** (`main`). Until
`release-desktop.yml` has been merged to `main`, the "Run workflow" button
won't fire it — so the pre-merge validation is done **locally** instead
(below). The first full CI build of the signed DMG therefore happens on the
first real release publish; the local checks de-risk the parts that
actually fail.

On the Mac that holds the cert, validate the two failure-prone pieces
directly (seconds, no build, no CI):

```bash
# (a) cert + private key usable for signing
security find-identity -v -p codesigning | grep "Developer ID Application"
#     → one line ending in your team name = good

# (b) notarization credentials authenticate (Apple ID + app-specific pw + team)
xcrun notarytool history \
  --apple-id "<APPLE_ID>" --password "<APPLE_PASSWORD>" --team-id "<APPLE_TEAM_ID>"
#     → "No submission history" (or a real list) = authenticated.
#       An HTTP 401 / "unable to authenticate" = a wrong credential.
```

Both green → the secrets are correct and a real release publish will sign,
notarize, and attach the DMG automatically. (Optionally, once the workflow
is on `main`, you can still run the `workflow_dispatch` build-only path to
exercise the full CI build before tagging.)

## Fallback behavior

If the secrets are absent (e.g. a fork), the workflow still builds an
**unsigned** DMG instead of failing — only Arkalogy's releases are signed.
A fork's users would see the old Gatekeeper warning; Arkalogy's don't.
