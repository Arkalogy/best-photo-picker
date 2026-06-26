# Security Model

This document describes the trust assumptions, threat model, and
extension points for Best Photo Picker's LAN sharing feature. If you
plan to harden bpp into a multi-user / internet-facing photo server,
start here — the abstractions below are designed to support that
evolution without rewriting the auth layer.

## Trust assumptions (today)

bpp is designed for **single-user, local-network** deployments:

- One owner. No user accounts.
- Library lives on the owner's machine.
- The Tauri desktop app authenticates via a per-boot **app session
  token** rendered into the page's `<meta name="auth-token">` tag.
- Phones / tablets on the same Wi-Fi authenticate via a **persistent
  share token** plus a **trusted-device fingerprint cookie** approved
  by the owner.
- Communication is plaintext **HTTP**. The LAN is assumed trusted
  (home Wi-Fi, not a coffee shop). **Disable LAN sharing on untrusted
  networks** — or deploy bpp behind a TLS-terminating reverse proxy
  (see *Extension hooks → 1. HTTPS* below). HTTP cleartext is a
  deliberate scope choice for the home-LAN use case, not an oversight.

## Threats addressed (on a trusted LAN)

The mitigations below assume the trust model above — single owner,
home Wi-Fi, plaintext HTTP. Sniffing on the LAN itself is *not*
addressed here; see "Threats NOT addressed" for the out-of-scope
list and the *Extension hooks → HTTPS* section for the reverse-proxy
hardening path.

| Threat | Mitigation |
|--------|------------|
| Random LAN attacker hits port 5001 | LAN gate (Settings → Share toggle) blocks all non-loopback when off |
| Attacker scans your QR (screenshot, etc.) and gets the URL | TOFU pairing — owner must approve the device on Mac |
| Friend who paired once now has perpetual access | Per-device "Revoke" button kicks them immediately |
| Revoked phone reloads → silent re-pending notifications on Mac | After revoke, soft-revival is *explicit*: phone user must tap "Request access again" — the row stays revoked across reloads |
| Phone bookmarks the URL, server reboots | Persistent share token survives restarts |
| Owner accidentally shares URL with the wrong person | "Revoke link" rotates the share token; all current URLs die |
| Token leaks via browser history | `history.replaceState` strips `?_token=` after first paint |
| Token leaks via Referer (outbound clicks) | `Referrer-Policy: no-referrer` on the index page |
| XSS exfiltrates the fingerprint cookie | `HttpOnly` cookie attribute |
| CSRF via cross-origin form | `SameSite=Lax` cookie + custom `X-Auth-Token` header check |
| Access log spammed by every phone API call | Dedup by (ip, user-agent) within a 10-minute window — one row per session |

## Threats NOT addressed

These are **out of scope** for the home-LAN use case but matter for
OSS contributors hardening the server:

- **Network-level eavesdropping (HTTP, not HTTPS).** Anyone running
  `tcpdump` on the LAN can sniff the share token from a request URL or
  header. Mitigation: TLS via reverse proxy (see hooks below).
- **Multi-user separation.** There's only one library access tier.
  Adding user accounts requires a real identity layer (passwords, JWT,
  OAuth). The schema reserves `share_devices.user_id` for this.
- **Per-album scoping of share links.** All trusted devices see the
  whole library. The schema reserves `share_devices.scope_json` for
  future per-album access.
- **Brute-force protection.** Token entropy (256 bits) makes brute force
  infeasible by design. There is no rate limiter; for an OSS-hardened
  deployment you'd want one.

## Auth boundary architecture

The single source of truth for "is this request authorized" is
`bpp/web/share.py:authorize_request(request, ctx) -> AuthResult`.

`AuthResult` carries two things:

- `decision`: `AuthDecision.ALLOW | DENY | PAIR_REQUIRED` — what the
  middleware acts on (200 / 403 / 403-with-pair-flag).
- `principal`: `Principal | None` — *who* authenticated, populated for
  `ALLOW` outcomes. The middleware stashes this on `flask.g.bpp_principal`
  so any blueprint handler can reach it.

The Flask middleware in `bpp/web/app.py` calls `authorize_request` on
every request and maps the decision to an HTTP response.

**To add a new auth scheme** (OAuth, JWT, API key for a CLI client),
add a new `Principal` kind and a branch inside `authorize_request`
that constructs it. The middleware doesn't need to change.

### The Principal shape

Today there's one user, but the auth layer already speaks two
schemes (Tauri's app-session token vs. the LAN share token + paired
fingerprint). We name them as `Principal` kinds so future flows slot
into the same dataclass without a wider refactor:

```python
@dataclass(frozen=True)
class Principal:
    kind: str                    # "local_app" | "lan_device" | "anonymous"
    fingerprint: str | None      # LAN_DEVICE only — for audit + scoped rules
    user_id: int | None          # reserved for future multi-user
    scopes: tuple[str, ...]      # reserved for future fine-grained perms
```

Current kinds:

| Kind | When | Notes |
|------|------|-------|
| `PRINCIPAL_LOCAL_APP` | Tauri / loopback with app token, or share token from loopback (escape hatch) | The dev machine. Full access. |
| `PRINCIPAL_LAN_DEVICE` | Phone via share token + trusted device fingerprint | `principal.fingerprint` set; logged in `share_access_log` automatically. |
| `PRINCIPAL_ANONYMOUS` | Static, index, `/api/v1/share/pair/{status,request}` | No identity required to load these. |

A future `PRINCIPAL_USER` (with `user_id` populated) is the v2 OAuth
hook. `Principal.user_id` and `Principal.scopes` are already on the
dataclass — adding multi-user means populating them from a session
store, not changing the shape.

Locked in by `tests/test_authorize_request.py::TestPrincipalIdentity`,
which forbids removing the forward-compat fields.

### Pairing state machine

A device's lifecycle in `share_devices`:

```
   ┌──────────────────────┐
   │  unknown (no row)    │
   └──────────┬───────────┘
              │ phone hits / with new fingerprint cookie
              ▼
   ┌──────────────────────┐
   │  pending (trusted_at │
   │  IS NULL,            │
   │  revoked_at IS NULL) │
   └──────┬───────┬───────┘
          │       │
   approve│       │block
          ▼       ▼
   ┌──────────┐ ┌──────────────────────────────┐
   │ trusted  │ │ revoked (revoked_at IS NOT   │
   │          │ │ NULL, prev_revoked = 1)      │
   └─────┬────┘ └──────┬───────────────────────┘
         │             │  POST /api/v1/share/pair/request
         │revoke       │  (explicit user tap on phone)
         ▼             ▼
       revoked       pending (with prev_revoked=1 sticky)
```

Two transitions matter for security UX:

- **Revoke is sticky by default.** A phone whose row is `revoked`
  reloads → middleware returns DENY → phone reloads → server
  *bumps last_seen* but does NOT auto-flip back to pending. The
  Mac side stays quiet. This prevents a stale cookie from spamming
  pending requests on every page load.
- **Re-request is explicit.** The phone's revoked terminal page has
  a "Request access again" button that POSTs to
  `/api/v1/share/pair/request`. The server runs `request_access(conn,
  fp)` which clears `revoked_at` and re-enters pending. Owner sees
  it with the `prev_revoked` tag for the rest of the device's
  lifetime.

The two paths exempt from the trusted-device gate are
`/api/v1/share/pair/status` (phone polls its own state) and
`/api/v1/share/pair/request` (phone re-asks). Both return only device-
state info — no library data — so it's safe to expose them to
untrusted LAN clients.

## Extension hooks (for OSS contributors)

### 1. HTTPS

`commands.py:cmd_serve` calls `app.run(host=..., port=...)`. Adding
TLS is a config change:

```python
ssl_context = config.get("ssl_context")  # ('/path/cert.pem', '/path/key.pem')
app.run(host=host, port=port, ssl_context=ssl_context, ...)
```

For real-world deployment, run bpp behind nginx / Caddy with
Let's Encrypt and treat bpp as a plain HTTP origin.

### 2. Reverse-proxy IP awareness

When bpp sits behind nginx, `request.remote_addr` is the proxy IP
(typically `127.0.0.1`), which means the loopback bypass would
trigger for everyone. The `behind_proxy` config flag wires
`werkzeug.middleware.proxy_fix.ProxyFix` so `X-Forwarded-For` is
honoured.

**`behind_proxy` requires `BPP_TRUSTED_PROXIES` to be set.** Vanilla
ProxyFix rewrites `REMOTE_ADDR` from the `X-Forwarded-For` header
unconditionally — so a public-internet client could spoof
`X-Forwarded-For: 127.0.0.1` and ProxyFix would oblige, promoting
the spoof to loopback and unlocking the owner SPA. Bpp's wrapper
only invokes ProxyFix when the **raw** upstream peer is in
`BPP_TRUSTED_PROXIES`. With an empty allowlist, `behind_proxy` is
a no-op and the server logs an error pointing at this gap.

```bash
# Example: nginx on the same host as bpp
export BPP_TRUSTED_PROXIES="127.0.0.1/32"
# config.yaml:  behind_proxy: true
```

Off by default (no proxy assumed). When you turn it on, set the
allowlist to match your reverse proxy's address — only RFC1918,
loopback, and link-local CIDRs are accepted (the safety filter
rejects `0.0.0.0/0`, `::/0`, and any range containing public IPs).

### 3. User accounts

`share_devices` already has `user_id INTEGER` (NULL today) and the
`Principal` dataclass (see "Auth boundary architecture" above)
already exposes `user_id` and `scopes` fields, currently unused.
When adding users:

1. Create a `users` table.
2. Backfill `share_devices.user_id` based on which library the
   device is paired with.
3. Add `PRINCIPAL_USER` and a branch in `authorize_request` that
   constructs `Principal(kind="user", user_id=..., scopes=...)`.
4. Existing handlers reach the principal via `flask.g.bpp_principal`;
   per-resource scope checks slot in there.

### 4. Database backend swap (Postgres / MySQL)

bpp ships with SQLite. Every dialect-specific operation goes
through `bpp.db.dialect.dialect`:

| Method | SQLite | Postgres |
|--------|--------|----------|
| `autoincrement_pk()` | `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGSERIAL PRIMARY KEY` |
| `json_extract(col, path)` | `json_extract(col, '$.k')` | `col->>'k'` (or `#>>` for nested) |
| `setup_connection(conn)` | WAL + foreign_keys + busy_timeout PRAGMAs | usually a no-op |
| `checkpoint(conn)` | `PRAGMA wal_checkpoint(TRUNCATE)` | no-op |
| `quick_check(conn)` | `PRAGMA quick_check` | no equivalent |
| `get/set_user_version(conn)` | `PRAGMA user_version` | dedicated `schema_meta` table |
| `column_names(conn, table)` | `PRAGMA table_info(...)` | `information_schema.columns` |
| `database_path(conn)` | `PRAGMA database_list` | n/a (use config) |

Adding Postgres support:

1. Subclass `DBDialect` in `bpp/db/dialect.py` → `PostgresDialect`.
2. Make the module-level `dialect` switchable via env var or config:
   ```python
   dialect: DBDialect = (
       PostgresDialect() if os.environ.get("BPP_DB") == "postgres"
       else SQLiteDialect()
   )
   ```
3. The 30+ files that previously inlined dialect-specific SQL now
   route through `dialect.X` calls — no further changes required.

The `tests/test_dialect.py::TestDBDialectABC` suite pins the required
methods; a partially-implemented subclass fails at instantiation
rather than at the first uncaught call site.

### 4. Per-album sharing

`share_devices.scope_json` is a NULL TEXT column. Future shape:

```json
{ "albums": [12, 34], "expires": 1735689600 }
```

`authorize_request` would gate per-album endpoints against the
device's scope. NULL = full library (today's behavior).

### 5. Rate limiting

Add a `flask-limiter` middleware around `_check_auth_token` to
throttle 4xx responses per-IP. Don't rate-limit successful auths —
trusted devices polling pair status are legitimate traffic.

### 6. Audit log retention

`share_access_log` is currently capped at 100 rows (FIFO). For
forensic / compliance use cases, swap to a time-bounded retention
(e.g. 30 days) and consider exporting to syslog.

### 7. Device pairing UX upgrades

The current TOFU pairing is owner-side approval (one click from
Settings → Share → Devices). For higher-friction-but-tighter security,
add a 4-digit code displayed on the phone that the owner types on the
Mac. Hook: a new `AuthDecision.PAIR_CODE_REQUIRED` and an additional
endpoint `POST /api/v1/share/devices/<id>/approve_code`.

## Configuration: environment variables

Environment variables that BPP reads at runtime (proxy trust,
cache paths, ONNX providers, memory caps, registry overrides) are
documented separately in [`docs/configuration.md`](configuration.md).
The only variable that interacts with the auth boundary is
`BPP_TRUSTED_PROXIES`; see *Extension hooks → 2. Reverse-proxy IP
awareness* above for how the loopback gate consumes it.

## Schema reference

```sql
CREATE TABLE share_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',          -- "iPhone Safari" etc.
    ip_at_pair TEXT NOT NULL DEFAULT '',
    user_id INTEGER,                        -- NULL today; future user accounts
    scope_json TEXT,                        -- NULL today; future per-album scoping
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    trusted_at INTEGER,                     -- NULL = pending
    revoked_at INTEGER,                     -- NULL = active
    prev_revoked INTEGER NOT NULL DEFAULT 0 -- sticky security cue
);

CREATE TABLE share_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    ip TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT ''
);
```

Settings (key/value):
- `lan_share_token` — persistent 256-bit share secret
- `lan_sharing_enabled` — "1" or "0"

## Test coverage

The auth boundary is locked in by ~120 tests across 9 files:

| File | What it covers |
|------|----------------|
| `tests/test_authorize_request.py` | Pure unit tests of the policy fn — every state transition |
| `tests/test_share_auth_gate.py` | Middleware integration: dual-token + LAN gate |
| `tests/test_share_devices.py` | DB layer: device state machine, request_access flow |
| `tests/test_share_cookie.py` | Fingerprint cookie attributes + Referrer-Policy + history.replaceState |
| `tests/test_pair_endpoints.py` | All `/api/v1/share/pair/*` and `/api/v1/share/devices/*` endpoints |
| `tests/test_share_endpoints.py` | Toggle / revoke / info |
| `tests/test_share_access_log.py` | Audit trail + middleware dedup |
| `tests/test_share_schema_migration.py` | v27 → v28 upgrade path (forward-compat columns intact) |
| `tests/test_share.py` | Lower-level helpers (LAN IP detection, share URL builder, QR PNG) |
| `tests-js/share-tab.module.test.mjs` | Settings → Share UI rendering + actions |

Run all share tests:

```
pytest tests/test_share*.py tests/test_authorize_request.py tests/test_pair_endpoints.py -q
npm run test:js -- share-tab
```

**Not covered automatically** (manual test loop):
- The pair.html inline JS (revoked-state Request-access button click)
- Full e2e revoke → tap → re-approve cycle on a real phone

The pair-page button presence is locked by a Python source-scan test
(`tests/test_js_source_scan.py::test_pair_template_has_request_access_button`)
so a future template refactor can't silently drop the recovery path.
