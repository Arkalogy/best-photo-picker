"""Check GitHub releases for available updates."""

from __future__ import annotations

import threading
import time
import urllib.request
from typing import Any

from bpp import __version__
from bpp.utils.json_utils import safe_json_loads
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_GITHUB_REPO = "Arkalogy/best-photo-picker"
_RELEASES_URL = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
_CACHE_TTL_OK = 86400  # 24 hours — happy path
_CACHE_TTL_ERROR = 300  # 5 minutes — retry sooner on transient failures
_REQUEST_TIMEOUT = 10  # seconds
# cap the response body so a compromised upstream (or a
# proxy serving an oversized payload) can't OOM the server. A typical
# GitHub release JSON is ~5-15 KB; 256 KB leaves comfortable headroom
# for unusually long release notes without giving an attacker arbitrary
# memory pressure on a forced poll.
_MAX_RESPONSE_BYTES = 256 * 1024  # 256 KiB

_cache_lock = threading.Lock()
_cached_result: dict[str, Any] | None = None
_cached_at: float = 0


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string like 'v1.2.3' or '1.2.3' into a tuple."""
    return tuple(int(x) for x in v.lstrip("v").split("."))


def _fetch_latest_release() -> dict[str, Any]:
    """Fetch the latest release from GitHub API.

    Reads at most ``_MAX_RESPONSE_BYTES`` from the response so a
    compromised upstream or rogue proxy can't OOM the server with a
    huge payload. If the upstream sets ``Content-Length`` above the
    cap we refuse before any read."""
    req = urllib.request.Request(
        _RELEASES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"BestPhotoPicker-UpdateChecker/{__version__}",
        },
    )
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        # Pre-flight on the advertised length: a server-claimed
        # Content-Length above the cap is an immediate refusal — no
        # point reading any of it. Stream-truncation below covers the
        # case where Content-Length is missing or lies.
        cl = resp.headers.get("Content-Length")
        if cl is not None:
            try:
                if int(cl) > _MAX_RESPONSE_BYTES:
                    log.warning(
                        "Update check: refusing oversized response (Content-Length=%s, cap=%d)",
                        cl,
                        _MAX_RESPONSE_BYTES,
                    )
                    return {}
            except ValueError:
                pass  # malformed header — fall through to bounded read
        # Read at most cap+1 so we can tell "exactly cap" from
        # "lied about the length and is actually larger." resp.read(N)
        # returns up to N bytes — there's no guarantee it stops at
        # exactly N, so we re-check the size after.
        body = resp.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            log.warning(
                "Update check: response exceeded %d bytes; truncating "
                "and refusing (likely upstream misbehaving).",
                _MAX_RESPONSE_BYTES,
            )
            return {}
        return safe_json_loads(body, {}, context="GitHub API")


def _classify_error(exc: BaseException) -> tuple[str, str]:
    """Map an exception to (short_code, user_facing_message).

    Codes are stable for the UI; messages are human-readable. We
    avoid leaking the underlying URL/host into user-facing strings —
    that's noise the user can't act on.
    """
    import json as _json
    from urllib.error import HTTPError, URLError

    if isinstance(exc, HTTPError):
        code = exc.code
        if code == 404:
            return ("not_found", "Release info unavailable (repo not found or private).")
        if code == 403:
            return ("rate_limited", "GitHub rate limit reached — try again in an hour.")
        if 500 <= code < 600:
            return ("server_error", f"GitHub is having problems (HTTP {code}).")
        return ("http_error", f"GitHub returned HTTP {code}.")
    if isinstance(exc, URLError):
        return ("network_error", "Couldn't reach GitHub (network or DNS issue).")
    if isinstance(exc, _json.JSONDecodeError):
        return ("malformed", "GitHub returned a response we couldn't parse.")
    if isinstance(exc, TimeoutError):
        return ("timeout", "Update check timed out.")
    return ("unknown_error", "Update check failed for an unexpected reason.")


def check_for_update(*, force: bool = False) -> dict[str, Any]:
    """Check if a newer version is available.

    Returns a dict with: status, available, current, latest, url,
    release_notes, error. ``status`` is "ok" or "error"; on error,
    ``error`` carries a short code and ``error_message`` a user-facing
    string. Successful checks cache for 24h; errors cache for 5min so
    transient issues recover quickly.
    """
    global _cached_result, _cached_at

    now = time.monotonic()
    with _cache_lock:
        if not force and _cached_result is not None:
            prev_status = _cached_result.get("status", "ok")
            ttl = _CACHE_TTL_OK if prev_status == "ok" else _CACHE_TTL_ERROR
            if (now - _cached_at) < ttl:
                return _cached_result

    current = __version__
    result: dict[str, Any] = {
        "status": "ok",
        "available": False,
        "current": current,
        "latest": current,
        "url": "",
        "release_notes": "",
        "error": "",
        "error_message": "",
    }

    try:
        release = _fetch_latest_release()
        tag = release.get("tag_name", "")
        if not tag:
            # _fetch_latest_release returns {} on size-cap refusal —
            # treat that as malformed rather than silently "up to date".
            result["status"] = "error"
            result["error"] = "malformed"
            result["error_message"] = "GitHub returned a response we couldn't parse."
        else:
            latest = tag.lstrip("v")
            result["latest"] = latest
            result["url"] = release.get("html_url", "")
            result["release_notes"] = release.get("body", "") or ""
            if _parse_version(latest) > _parse_version(current):
                result["available"] = True

    except Exception as exc:
        # Bug #11 (UAT 2026-06-01): the activity-log dropdown surfaces
        # the WARNING message verbatim. Dumping a Python traceback there
        # alarms users — they see 50 lines of urllib internals instead
        # of 'check failed, will retry.' The classified human message is
        # what belongs in the WARNING; the traceback still lands in
        # server.log via the separate DEBUG line below for dev triage.
        code, msg = _classify_error(exc)
        # "not_found" is the pre-release / private-repo case — the
        # bppicker repo isn't published yet, so the GitHub Releases
        # API returns 404. That's expected for any dev / pre-release
        # checkout and shouldn't fire a WARNING. Real network /
        # parse failures still warn.
        (log.info if code == "not_found" else log.warning)("Update check failed: %s", msg)
        log.debug("Update check traceback", exc_info=True)
        result["status"] = "error"
        result["error"] = code
        result["error_message"] = msg

    with _cache_lock:
        _cached_result = result
        _cached_at = time.monotonic()

    return result
