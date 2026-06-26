# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security bugs.

The preferred channel is **[GitHub Private Vulnerability Reporting](https://github.com/Arkalogy/best-photo-picker/security/advisories/new)** — click "Report a vulnerability" on that page and GitHub creates a private security advisory. This keeps the report off the public timeline while we investigate, and the advisory becomes the workspace for the fix + CVE coordination.

If GitHub PVR is unavailable for any reason, file a public issue with the `[security]` tag containing only "I'd like to report a security issue, please contact me" plus a way to reach you — the maintainer will reply to set up a private channel before you disclose any details. Do **not** include reproduction steps in the public issue.

For urgent security matters you can also email **support@arkalogy.com**. (GitHub's `users.noreply.github.com` addresses don't deliver mail — don't email those.)

**What to include:**
- Steps to reproduce (or a minimal proof-of-concept)
- The version you tested against (`bpp --version` or commit SHA)
- The deployment shape (loopback Tauri / `bpp serve --library` / Docker / LAN sharing on)
- Whether you've already disclosed this elsewhere

**What to expect:**
- Acknowledgement within 48 hours
- A coordinated disclosure timeline before any public mention
- Credit in the release notes / advisory unless you ask to remain anonymous

## Threat Model & Trust Assumptions

The full threat model — trust assumptions, scope, what's in/out of bounds, and the extension hooks for hardening into a multi-user or internet-facing deployment — lives in [`docs/security.md`](docs/security.md). The summary below is what's true for the v0.1.x line:

**Designed for single-user, local-network deployments**:

- **Loopback (Tauri desktop / local browser)** authenticates via a per-boot app session token rendered into the page's `<meta name="auth-token">` tag.
- **LAN clients (phones, tablets, other devices on the same Wi-Fi)** authenticate via a *persistent* share token **plus** a TOFU (Trust-On-First-Use) device-pairing flow — the owner explicitly approves each new device from the Mac before it can browse the library. Approval is per-fingerprint cookie, not per-token.
- **No telemetry or cloud account.** Photo originals and library contents are not uploaded for analysis, scoring, or selection. External network calls are limited to documented app features:
  - **Model downloads** — fetched from public model hosts (HuggingFace, Google Storage, OpenAI's Azure CDN for the CLIP tokenizer vocab, GitHub releases for Ultralytics/YOLO) on first-use of a feature that needs a missing model. **All** bpp-managed model downloads — including the LaMa inpainting weights (only when `bppicker[inpaint]` is installed and the user clicks "Remove object") — enforce a SHA-256 pin + 120s timeout via `bpp.utils.download.download_file()`. For LaMa specifically, bpp pre-fetches the weights itself and passes the verified path to `simple_lama_inpainting` via its documented `LAMA_MODEL` env-var override, so the library's unsafe internal `torch.hub.download_url_to_file(..., hash_prefix=None)` path never runs.
  - **Runtime dependency installs** — only when the user clicks Install in Settings → Advanced → ML Models, the app shells out to `pip install` to fetch optional packages (face recognition, NudeNet, ONNX Runtime, AI inpainting) from PyPI. TLS-only trust; transitive dependencies are NOT hash-pinned, so the user is implicitly trusting PyPI to the same degree as `pip install` from a terminal would. A future revision could pin SHA-256 hashes via a generated requirements file.
  - **Update check** — calls api.github.com to see if a new version is out; can be turned off in Settings → App.
  - **Map tiles** — only when viewing photo locations (Map view or lightbox for a geo-tagged photo): OpenStreetMap tile servers are queried with photo coordinates.
  - **LAN sharing** — only when explicitly enabled; never traverses the internet (LAN-only by design).
- **Plugins are off by default and load only with explicit opt-in.** Any package on the venv path that declares a `bpp.plugins` setuptools entry-point can run arbitrary `setup()` code at process start, with the same privileges as bpp itself — filesystem, network, library access. To prevent a malicious or compromised package from auto-executing, plugin loading requires `BPP_ENABLE_PLUGINS=1` to be set in the environment. Without it, `bpp.plugins` entry-points are ignored and a one-shot INFO log records why. Plugin authoring contract + extension-point list lives in [`docs/plugins.md`](docs/plugins.md). Treat plugins like editor extensions: install only from sources you trust.
- **All metadata is local SQLite**, with WAL mode + atomic backups before mutating migrations.
- **HTTP, not HTTPS**, by default. The threat model assumes a trusted LAN. For untrusted networks (coffee shops, conference Wi-Fi), don't enable LAN sharing — or run bpp behind a TLS-terminating reverse proxy. The auth layer in `bpp/web/share.py` exposes a clean seam for swapping in OAuth/JWT/proxy-auth without rewriting the Flask integration; see `docs/security.md` for the principal/scope shape.

**In scope** for security reports:
- Authentication / authorization bypasses (app token, share token, device trust)
- Path traversal / arbitrary file access via API parameters
- Injection (SQL, command, template) in any blueprint
- Token leakage in logs, response bodies, or front-end state
- LAN-share token reuse / replay attacks
- Subprocess sandbox escapes (analyze worker, video trim, ffmpeg)

**Out of scope**:
- Rate-limiting on a single-owner local-network app (not a hardening target for v0.1.x)
- DoS via expensive CPU work — the photo collection itself is the workload; analysis cost is by-design unbounded
- Anything requiring physical access to the host machine
- Issues in third-party dependencies — please report those upstream first
