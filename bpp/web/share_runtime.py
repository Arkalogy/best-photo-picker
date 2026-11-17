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
from bpp.web.share_seal import KEY_LAN_ENABLED as _KEY_LAN_ENABLED  # noqa: E402

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
_DESTRUCTIVE_BUCKETS_MAX = 1024
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
        if len(_DESTRUCTIVE_BUCKETS) >= _DESTRUCTIVE_BUCKETS_MAX:
            # Buckets untouched for a while are full again: forgetting
            # them changes nothing. (Keys include caller addresses.)
            idle = [k for k, (_t, at) in _DESTRUCTIVE_BUCKETS.items() if now - at > 120]
            for key in idle or list(_DESTRUCTIVE_BUCKETS)[: _DESTRUCTIVE_BUCKETS_MAX // 4]:
                del _DESTRUCTIVE_BUCKETS[key]
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
    with _ACCESS_LOGGED_LOCK:
        _ACCESS_LOGGED.clear()


# ──────────────────────────────────────────────────────────────────
# Share access log — DB-backed audit trail
# ──────────────────────────────────────────────────────────────────


# Hard cap on the share access log table. Last N rows visible to the
# user; oldest rows pruned on insert. Small bound so we never grow it
# without thinking — this is an audit aid, not analytics.
_ACCESS_LOG_MAX_ROWS = 100


_ACCESS_LOG_DEDUP_WINDOW_S = 10 * 60  # 10 minutes


#: Rows one device keeps in the access log (its latest visits).
_ACCESS_LOG_ROWS_PER_DEVICE = 3


#: (fingerprint, ip) -> when that device's visit was last written down.
_ACCESS_LOGGED: dict[tuple[str, str], float] = {}
_ACCESS_LOGGED_LOCK = threading.Lock()
_ACCESS_LOGGED_MAX = 512


def _already_logged(fingerprint: str, ip: str) -> bool:
    """Whether this device (from this address) was logged within the
    window; if not, note that it is being logged now. In memory: this
    runs on every request a device makes."""
    now = time.monotonic()
    with _ACCESS_LOGGED_LOCK:
        last = _ACCESS_LOGGED.get((fingerprint, ip))
        if last is not None and now - last < _ACCESS_LOG_DEDUP_WINDOW_S:
            return True
        if len(_ACCESS_LOGGED) >= _ACCESS_LOGGED_MAX:
            for key in sorted(_ACCESS_LOGGED, key=_ACCESS_LOGGED.__getitem__)[:64]:
                del _ACCESS_LOGGED[key]
        _ACCESS_LOGGED[(fingerprint, ip)] = now
        return False


def record_share_access(
    conn: sqlite3.Connection, *, ip: str, user_agent: str, fingerprint: str | None = None
) -> None:
    """Append a paired device's visit to the access log.

    One row per device and address within a 10-minute window: a page
    load fires dozens of API calls, the owner wants "iPhone at 09:36".
    The device is told apart by *fingerprint* (what the owner approved),
    and the row carries the name it was approved under; the User-Agent
    is only a fallback, because the device sends it and could put any
    text, or a new one each request, there. Rows under one name keep the
    latest few, so a device can't push the others out; the whole log
    keeps the last `_ACCESS_LOG_MAX_ROWS`.
    """
    label = user_agent or ""
    if fingerprint:
        if _already_logged(fingerprint, ip):
            return
        row = conn.execute(
            "SELECT name FROM share_devices WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if row is not None and row[0]:
            label = str(row[0])
    now = int(time.time())
    cutoff = now - _ACCESS_LOG_DEDUP_WINDOW_S
    existing = conn.execute(
        "SELECT 1 FROM share_access_log WHERE ip = ? AND user_agent = ? AND ts > ? LIMIT 1",
        (ip, label, cutoff),
    ).fetchone()
    if existing is not None and not fingerprint:
        return  # already logged within the window
    conn.execute(
        "INSERT INTO share_access_log (ts, ip, user_agent) VALUES (?, ?, ?)",
        (now, ip, label),
    )
    conn.execute(
        "DELETE FROM share_access_log WHERE user_agent = ? AND id NOT IN ("
        "  SELECT id FROM share_access_log WHERE user_agent = ? ORDER BY ts DESC, id DESC LIMIT ?"
        ")",
        (label, label, _ACCESS_LOG_ROWS_PER_DEVICE),
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
    """Whether the user has flipped the Settings → Share toggle on.
    Off while this installation's key can't be read: the stored switch
    can't be told from one that came with the library."""
    from bpp.db.settings import get_setting
    from bpp.web import share_seal_store

    return get_setting(conn, _KEY_LAN_ENABLED) == "1" and not share_seal_store.unreadable()


def set_lan_sharing_enabled(conn: sqlite3.Connection, enabled: bool) -> None:
    """Persist the on/off toggle. Survives restarts."""
    from bpp.web.share_seal import set_sealed

    set_sealed(conn, _KEY_LAN_ENABLED, "1" if enabled else "0")


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
