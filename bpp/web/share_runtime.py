"""Runtime concerns for LAN sharing: rate limiting, access log, LAN URL.

Extracted from share.py to drop the auth-policy file below the 500 LOC
soft cap. These three sub-concerns share a "runtime/operational" theme
but are otherwise independent of each other and of the rest of share.py:

* **Rate-limit token buckets** (in-memory, per-IP for pair requests,
  per-principal for destructive endpoints). Lost on restart by design —
  restart already invalidates lots of share state.
* **Access log** — DB-backed audit trail of successful share-token
  auths, deduplicated within a 10-minute window.
* **LAN config + URL** — Settings → Share toggle persistence and the
  one-shot helpers that detect the LAN-routable IP and assemble the
  tokenized share URL.

share.py re-exports everything that has external callers
(``consume_pair_request_token``, ``consume_destructive_token``,
``record_share_access``, ``recent_share_access``,
``is_lan_sharing_enabled``, ``set_lan_sharing_enabled``,
``detect_lan_ip``, ``get_lan_share_url``, and the
``_reset_pair_request_buckets_for_tests`` hook).
"""

from __future__ import annotations

import socket
import sqlite3
import threading
import time
from urllib.parse import quote

from bpp.utils.logging import get_logger

log = get_logger(__name__)


# DB settings key for the LAN sharing on/off toggle. Mirrors the
# parallel _KEY_SHARE_TOKEN in share.py — both are stored as strings
# in the generic key/value settings table.
_KEY_LAN_ENABLED = "lan_sharing_enabled"


# ──────────────────────────────────────────────────────────────────
# Rate limiting
# ──────────────────────────────────────────────────────────────────


# Token bucket for /api/share/pair/request — defends against an attacker
# (or buggy client) flooding the endpoint with random fingerprints to
# spam the owner's pending-requests list. Per-IP, in-memory, monotonic.
# Generous capacity for a real phone tapping the button.
_PAIR_REQUEST_RATE = 10 / 60.0  # 10 requests per minute steady-state
_PAIR_REQUEST_BURST = 10  # initial bucket capacity
_PAIR_REQUEST_BUCKETS: dict[str, tuple[float, float]] = {}
_PAIR_REQUEST_LOCK = threading.Lock()


def consume_pair_request_token(ip: str) -> bool:
    """Per-IP token bucket. Returns True if the IP can make a request now,
    False if rate-limited. State is in-memory (lost on restart, which is
    fine — restart already invalidates lots of share state)."""
    now = time.monotonic()
    with _PAIR_REQUEST_LOCK:
        tokens, last = _PAIR_REQUEST_BUCKETS.get(ip, (float(_PAIR_REQUEST_BURST), now))
        tokens = min(float(_PAIR_REQUEST_BURST), tokens + (now - last) * _PAIR_REQUEST_RATE)
        if tokens < 1:
            _PAIR_REQUEST_BUCKETS[ip] = (tokens, now)
            return False
        _PAIR_REQUEST_BUCKETS[ip] = (tokens - 1, now)
        return True


# Per-principal rate limit on destructive endpoints (POST/PUT/DELETE
# under /api/). Defends against:
#   - hostile paired phone slamming /api/v1/photos/delete-permanent
#   - infinite-loop client bug spamming /api/v1/analyze
#   - stuck retry logic that never gives up
# Generous defaults: 60/minute steady, 60 burst — enough for legit
# batch operations (deleting 30 photos), tight enough that a runaway
# client gets throttled within seconds.
_DESTRUCTIVE_RATE = 60 / 60.0
_DESTRUCTIVE_BURST = 60
_DESTRUCTIVE_BUCKETS: dict[str, tuple[float, float]] = {}
_DESTRUCTIVE_LOCK = threading.Lock()


def consume_destructive_token(principal_key: str) -> bool:
    """Per-principal token bucket for state-changing endpoints.

    `principal_key` is the LAN device fingerprint, or "local_app" for
    loopback / app-token requests, or the IP for anonymous (which
    shouldn't happen on destructive endpoints since auth already ran).

    Returns True if a token was consumed (request may proceed),
    False if rate-limited.
    """
    now = time.monotonic()
    with _DESTRUCTIVE_LOCK:
        tokens, last = _DESTRUCTIVE_BUCKETS.get(principal_key, (float(_DESTRUCTIVE_BURST), now))
        tokens = min(float(_DESTRUCTIVE_BURST), tokens + (now - last) * _DESTRUCTIVE_RATE)
        if tokens < 1:
            _DESTRUCTIVE_BUCKETS[principal_key] = (tokens, now)
            return False
        _DESTRUCTIVE_BUCKETS[principal_key] = (tokens - 1, now)
        return True


def _reset_pair_request_buckets_for_tests() -> None:
    """Test hook — reset rate-limit state between tests."""
    with _PAIR_REQUEST_LOCK:
        _PAIR_REQUEST_BUCKETS.clear()
    with _DESTRUCTIVE_LOCK:
        _DESTRUCTIVE_BUCKETS.clear()


# ──────────────────────────────────────────────────────────────────
# Share access log — DB-backed audit trail
# ──────────────────────────────────────────────────────────────────


# Hard cap on the share access log table. Last N rows visible to the
# user; oldest rows pruned on insert. Small bound so we never grow it
# without thinking — this is an audit aid, not analytics.
_ACCESS_LOG_MAX_ROWS = 100


_ACCESS_LOG_DEDUP_WINDOW_S = 10 * 60  # 10 minutes


def record_share_access(conn: sqlite3.Connection, *, ip: str, user_agent: str) -> None:
    """Append a successful share-token auth to the access log.

    Deduplicated by (ip, user_agent) within a 10-minute window — a
    single page load fires dozens of API calls, but the user only
    cares about "iPhone connected at 09:36" not 30 separate rows.
    Pruning is inline: after insert, keep only the last
    `_ACCESS_LOG_MAX_ROWS` entries.
    """
    now = int(time.time())
    cutoff = now - _ACCESS_LOG_DEDUP_WINDOW_S
    existing = conn.execute(
        "SELECT 1 FROM share_access_log WHERE ip = ? AND user_agent = ? AND ts > ? LIMIT 1",
        (ip, user_agent or "", cutoff),
    ).fetchone()
    if existing is not None:
        return  # already logged within the window
    conn.execute(
        "INSERT INTO share_access_log (ts, ip, user_agent) VALUES (?, ?, ?)",
        (now, ip, user_agent or ""),
    )
    conn.execute(
        "DELETE FROM share_access_log WHERE id NOT IN ("
        "  SELECT id FROM share_access_log ORDER BY ts DESC LIMIT ?"
        ")",
        (_ACCESS_LOG_MAX_ROWS,),
    )
    conn.commit()


def recent_share_access(conn: sqlite3.Connection, *, limit: int = 10) -> list[dict[str, object]]:
    """Return the most recent share-token auths, newest first."""
    rows = conn.execute(
        "SELECT ts, ip, user_agent FROM share_access_log ORDER BY ts DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"ts": r[0], "ip": r[1], "user_agent": r[2]} for r in rows]


# ──────────────────────────────────────────────────────────────────
# LAN config + URL
# ──────────────────────────────────────────────────────────────────


def is_lan_sharing_enabled(conn: sqlite3.Connection) -> bool:
    """Whether the user has flipped the Settings → Share toggle on."""
    from bpp.db.settings import get_setting

    return get_setting(conn, _KEY_LAN_ENABLED) == "1"


def set_lan_sharing_enabled(conn: sqlite3.Connection, enabled: bool) -> None:
    """Persist the on/off toggle. Survives restarts."""
    from bpp.db.settings import set_setting

    set_setting(conn, _KEY_LAN_ENABLED, "1" if enabled else "0")


def detect_lan_ip() -> str | None:
    """Best-effort detect the LAN-routable IPv4 address.

    Uses the well-known UDP-connect trick: a connect() to a public
    address forces the OS to pick the outbound interface, which is the
    one we want to advertise on the LAN. No packets are actually sent
    (UDP is connectionless). Returns None if no network is reachable.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        # 8.8.8.8:53 is Google DNS — never reached, just used to pick interface.
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        if isinstance(ip, str) and not ip.startswith("127."):
            return ip
        return None
    except OSError:
        return None
    finally:
        s.close()


def get_lan_share_url(port: int, auth_token: str, ip: str | None = None) -> str:
    """Build the LAN-shareable URL with auth token embedded.

    If `ip` is None, attempts to auto-detect via detect_lan_ip().
    Falls back to a placeholder host if no LAN IP is reachable.
    The token is URL-quoted defensively even though token_hex() output
    is hex-only — keeps the helper safe if the format ever changes.
    """
    if ip is None:
        ip = detect_lan_ip() or "<your-lan-ip>"
    return f"http://{ip}:{port}/?_token={quote(auth_token, safe='')}"
