"""Fetch the remote registry manifest at startup.

Batch 8 / item 12 of the legal-posture rollout. Pairs with the
signed-manifest verifier in :mod:`bpp.registry.signed_manifest` and
the overlay merger in :mod:`bpp.registry.overlay`. This module is
the small, conservative HTTPS client that fetches the manifest
bytes — everything else (verification, application) happens
in-process against the returned bytes.

What the fetcher enforces

1. **HTTPS only.** The fetch refuses any URL whose scheme is not
   ``https``. TLS authenticates the upstream Arkalogy registry
   host; downgrading to plain HTTP would let an on-path attacker
   substitute their own manifest bytes, defeating both the
   transport-level trust and the canonical-signature trust
   downstream (the signature would obviously fail, but the user
   would see a fetch error rather than a tampering alert if the
   bytes never arrived).
2. **Source-domain allowlist.** Only URLs whose host is on a
   small fixed allowlist are accepted. Today the only allowed
   host is ``arkalogy.github.io`` — the published GitHub Pages
   endpoint for the ``bppicker-registry`` repo. New hosts get
   added by editing this file (and the test that pins the list)
   in a code review.
3. **No redirects to unallowed hosts.** ``urllib.request`` will
   transparently follow HTTP redirects; this fetcher uses a
   custom redirect handler that re-checks the destination against
   the allowlist on every hop and refuses the fetch if any
   intermediate host fails the check.
4. **Hard timeout** so a hung TLS handshake or slow byte stream
   does not block startup forever. 10 seconds is the default;
   tests can override via ``BPP_REMOTE_REGISTRY_TIMEOUT``.

Best-effort failure

Every failure path (DNS, TLS, HTTP error, timeout, response too
large) returns ``None`` and logs a warning. BPP startup must not
abort because the remote registry is unreachable; the bundled
baseline is the source of truth when the network fails.

What this module does NOT do

* Cache the manifest on disk. The overlay system runs each
  startup; if the user is offline, the bundled baseline is what
  they get. A future hardening pass can add a signed-on-disk
  cache so the most recent good manifest survives offline
  startup, but Batch 8 keeps the surface area small.
* Decode or apply the manifest. That is
  :func:`bpp.registry.signed_manifest.verify_manifest` plus
  :func:`bpp.registry.overlay.apply_overlay`.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from typing import Any

from bpp.utils.logging import get_logger

_log = get_logger(__name__)


#: The published GitHub Pages endpoint for the bppicker-registry repo
#: (Q4 / item 12 + 23). New hosts land here only via a code review.
DEFAULT_REMOTE_REGISTRY_URL = "https://arkalogy.github.io/bppicker-registry/registry.json"


#: Hosts the fetcher will accept. The fetch also refuses non-HTTPS
#: URLs regardless of host.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "arkalogy.github.io",
    }
)


def _resolve_effective_url() -> str:
    """Return the URL the overlay fetch should use.

    Production: ``DEFAULT_REMOTE_REGISTRY_URL``. The
    ``BPP_REMOTE_REGISTRY_URL`` env var overrides this — meant for
    local mocking (see ``scripts/run_mock_registry.py``) and for
    air-gapped deployments that ship their own internal mirror.

    The HTTPS-only + host-allowlist checks still apply unless the
    explicit insecure escape hatch (:func:`_insecure_mode_enabled`)
    is also set. The signature check is mandatory regardless — a
    URL override does NOT weaken the cryptographic boundary.
    """
    return os.environ.get("BPP_REMOTE_REGISTRY_URL") or DEFAULT_REMOTE_REGISTRY_URL


def _insecure_mode_enabled() -> bool:
    """Return ``True`` when ``BPP_REMOTE_REGISTRY_INSECURE`` is set.

    DEV-ONLY escape hatch: allows ``http://`` URLs and bypasses
    the host allowlist. Every fetch under insecure mode logs a
    WARNING line so it's impossible to miss in a server.log if it
    leaked into production.

    The signature check stays mandatory — even in insecure mode a
    tampered manifest still fails verification and the bundled
    baseline wins. The transport-layer rules are belt-and-
    suspenders; the cryptographic boundary is the actual security
    boundary."""
    return bool(os.environ.get("BPP_REMOTE_REGISTRY_INSECURE"))


#: Maximum response size in bytes. The manifest is a small JSON
#: document — 1 MB is generous and prevents a malicious or
#: misconfigured server from feeding us a 10 GB response.
MAX_RESPONSE_BYTES = 1_000_000


#: Default network timeout in seconds. Override via env var for
#: tests that need to assert the timeout path.
def _resolve_timeout() -> float:
    raw = os.environ.get("BPP_REMOTE_REGISTRY_TIMEOUT")
    if raw:
        try:
            return float(raw)
        except ValueError:
            _log.warning(
                "BPP_REMOTE_REGISTRY_TIMEOUT=%r is not a number; using default 10s",
                raw,
            )
    return 10.0


def _is_allowed_url(url: str) -> tuple[bool, str]:
    """Validate ``url`` against the HTTPS-only + host-allowlist
    rules. Returns ``(allowed, reason)``.

    When :func:`_insecure_mode_enabled` returns True (dev env var),
    ``http://`` is allowed and the host allowlist is bypassed. The
    signature check stays mandatory regardless."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        return False, f"unparseable URL: {exc}"
    insecure = _insecure_mode_enabled()
    if parsed.scheme not in ("https", "http"):
        return False, (
            f"refusing URL {url!r}; only https:// (or http:// in "
            "insecure mode) is allowed for the remote registry manifest"
        )
    if parsed.scheme == "http" and not insecure:
        return False, (
            f"refusing non-HTTPS URL {url!r}; only https:// is allowed for "
            "the remote registry manifest. Set BPP_REMOTE_REGISTRY_INSECURE=1 "
            "for local mocking only."
        )
    if not parsed.hostname:
        return False, f"URL {url!r} has no hostname"
    host = parsed.hostname.lower()
    if host not in ALLOWED_HOSTS and not insecure:
        return False, (
            f"host {host!r} is not on the remote-registry allowlist "
            f"{sorted(ALLOWED_HOSTS)}; refusing fetch"
        )
    return True, ""


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block HTTP redirects to hosts that fail the allowlist check.

    ``urllib`` by default transparently follows 301/302 redirects.
    A compromised registry could 302 us to a malicious host; this
    handler re-checks every intermediate hop against the same
    allowlist the original URL had to clear.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,  # type: ignore[type-arg]
        code: int,
        msg: str,
        headers: Any,  # type: ignore[type-arg]
        newurl: str,
    ) -> urllib.request.Request | None:
        allowed, reason = _is_allowed_url(newurl)
        if not allowed:
            _log.warning(
                "Refusing remote-registry redirect to disallowed URL %s: %s",
                newurl,
                reason,
            )
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                f"refused redirect to {newurl}: {reason}",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_remote_manifest(
    url: str | None = None,
    *,
    timeout_seconds: float | None = None,
    allowed_hosts: Iterable[str] | None = None,
) -> bytes | None:
    """Fetch the remote-registry manifest bytes.

    Returns the response body as bytes on success or ``None`` on
    any failure (DNS, TLS, HTTP error, allowlist refusal, too-
    large response, timeout). Failures log at WARNING; the caller
    proceeds with the bundled baseline.

    The arguments default to the production allowlist and a
    sensible timeout. Tests pass a smaller allowlist + tighter
    timeout to exercise the failure paths without monkey-patching
    the constants.

    When ``BPP_REMOTE_REGISTRY_URL`` is set, that URL overrides the
    default (used for local mocking — see ``scripts/run_mock_registry.py``).
    When ``BPP_REMOTE_REGISTRY_INSECURE=1`` is set, http:// is allowed
    and the host allowlist is bypassed — DEV ONLY, logs WARNING every
    time the fetch runs in that mode.
    """
    # Allow tests to relax the allowlist via the argument; production
    # always uses the module-level allowlist.
    eff_allowed = (
        frozenset(h.lower() for h in allowed_hosts) if allowed_hosts is not None else ALLOWED_HOSTS
    )
    eff_timeout = timeout_seconds if timeout_seconds is not None else _resolve_timeout()
    effective_url = url if url is not None else _resolve_effective_url()
    if _insecure_mode_enabled():
        _log.warning(
            "BPP_REMOTE_REGISTRY_INSECURE is set — http:// allowed, host "
            "allowlist bypassed. Signature check still mandatory. DO NOT "
            "enable in production."
        )

    allowed, reason = _is_allowed_url(effective_url)
    if not allowed:
        _log.warning("Remote registry fetch refused: %s", reason)
        return None
    parsed = urllib.parse.urlparse(effective_url)
    host = (parsed.hostname or "").lower()
    # Per-call allowlist (for tests) still applies as an extra narrowing
    # in non-insecure mode. Insecure mode bypasses both.
    if not _insecure_mode_enabled() and allowed_hosts is not None and host not in eff_allowed:
        _log.warning(
            "Remote registry fetch refused: host %r not in per-call allowlist %s",
            host,
            sorted(eff_allowed),
        )
        return None

    opener = urllib.request.build_opener(_AllowlistedRedirectHandler())
    try:
        with opener.open(
            urllib.request.Request(
                effective_url,
                headers={
                    "User-Agent": "bppicker/registry-fetcher",
                    "Accept": "application/json",
                },
            ),
            timeout=eff_timeout,
        ) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        _log.warning(
            "Remote registry fetch failed for %s: %s (using bundled baseline)",
            effective_url,
            exc,
        )
        return None
    if len(data) > MAX_RESPONSE_BYTES:
        _log.warning(
            "Remote registry response from %s exceeded %d bytes; refusing "
            "to load (using bundled baseline)",
            effective_url,
            MAX_RESPONSE_BYTES,
        )
        return None
    return data
