"""Trusted-proxy CIDR allowlist for the LAN gate.

The trusted-proxy config promotes a remote IP to "loopback" treatment
for LAN-gate purposes, which controls whether the owner SPA renders
with the per-boot app session token. A misconfig is a privilege-
escalation primitive (e.g. ``BPP_TRUSTED_PROXIES=0.0.0.0/0`` would
hand the owner SPA to *any* remote), so this module's safety filter
rejects unsafe CIDRs and the once-per-startup warning makes the
operator-visible mistake explicit.

Extracted from share.py to keep the auth surface focused. The
helpers and their backing state (_WARNED_CIDRS memo, the
``BPP_TRUSTED_PROXIES`` / ``BPP_TRUST_PROXY`` env-var contracts) all
live here. share.py re-exports the public names for backwards
compatibility.
"""

from __future__ import annotations

import ipaddress as _ipaddress
import os
import threading

from bpp.utils.logging import get_logger

log = get_logger(__name__)


# Trusted-peer allowlist — replaces the original BPP_TRUST_PROXY=1
# boolean flag, which was unsafe: with the container published as
# `-p 0.0.0.0:5001:5001`, *any* LAN client got owner/app-token
# treatment. The CIDR-list shape forces an explicit decision about
# which immediate peers may forward identity.
#
# BPP_TRUSTED_PROXIES is a comma-separated list of CIDRs. Only when
# the immediate peer's IP falls inside one of these CIDRs is the
# request treated as loopback for LAN-gate purposes.
#
# Default for the Docker image (set in the Dockerfile):
#   172.16.0.0/12,192.168.65.0/24
# That covers the standard Docker bridge gateway range and Docker
# Desktop's host-loopback alias on macOS / Windows. **The container
# MUST be bound to host loopback (`-p 127.0.0.1:5001:5001`)** — if
# it's bound to 0.0.0.0:5001, LAN clients in the bridge range would
# also be inside the allowlist (false positive). The Dockerfile
# comments document this.
#
# Bare-metal installs leave this unset; the loopback IPv4/IPv6
# checks are sufficient there.
_TRUSTED_PROXIES_ENV = "BPP_TRUSTED_PROXIES"
# Legacy boolean — kept for one minor version with a startup warning;
# rejected silently if the user hasn't migrated.
_LEGACY_TRUST_PROXY_ENV = "BPP_TRUST_PROXY"


_CGNAT_BLOCK = _ipaddress.ip_network("100.64.0.0/10")


def _is_safe_proxy_network(net) -> bool:
    """Policy filter: which CIDRs may a trusted-proxy operator declare?

    The trusted-proxy config promotes a remote IP to "loopback" for
    LAN-gate purposes, which controls whether the owner SPA renders
    with the per-boot app session token. A misconfig here is a
    privilege-escalation primitive — `BPP_TRUSTED_PROXIES=0.0.0.0/0`
    would hand the owner SPA to *any* remote.

    `prefixlen == 0` (the catch-all networks) is rejected first
    because `is_private` is False for them on Python 3.11+ but the
    intent is unambiguous.
    """
    if net.prefixlen == 0:
        return False
    # RFC6598 CGNAT (100.64/10): not RFC1918, so is_private is False
    # on Python 3.11, but it's a well-known non-routable range used
    # legitimately in AWS ECS / Fargate and ISP CGN deployments.
    if net.version == 4:
        try:
            if net.subnet_of(_CGNAT_BLOCK):
                return True
        except TypeError:
            pass
    # `is_private` covers RFC1918, the 169.254/16 link-local block,
    # 192.0.0.0/29, and a few others. `is_loopback` covers 127/8
    # and ::1. `is_link_local` covers fe80::/10. Together they form
    # the "this is a host-local or private-LAN range" predicate.
    return net.is_private or net.is_loopback or net.is_link_local


# Set of bad-CIDR chunks we've already complained about. The parser
# below re-runs on every request (so env changes are observable in
# tests), but we only want to log each unique misconfig ONCE per
# startup — otherwise an operator with `BPP_TRUSTED_PROXIES=0.0.0.0/0`
# and LAN sharing on gets the same warning N times per phone poll.
# Keyed on the raw chunk string so `0.0.0.0/0` and `   0.0.0.0/0   `
# share a slot after the .strip().
_WARNED_CIDRS: set[str] = set()
_WARNED_CIDRS_LOCK = threading.Lock()


def _warn_bad_cidr_once(chunk: str, message: str, *args: object) -> None:
    """Log `message % args` at WARNING the first time `chunk` is seen.

    Subsequent calls with the same chunk are silent. Thread-safe so
    concurrent requests can't race past each other and double-log.
    """
    with _WARNED_CIDRS_LOCK:
        if chunk in _WARNED_CIDRS:
            return
        _WARNED_CIDRS.add(chunk)
    log.warning(message, *args)


def _trusted_peer_networks() -> tuple:
    """Parse `BPP_TRUSTED_PROXIES` into a tuple of ip_network objects.

    Re-parses on every call (no module-level cache) so a test that
    mutates `os.environ` sees the new config. Invalid CIDR entries
    are dropped with a log warning rather than failing the request —
    the operator sees the warning ONCE per bad value (see
    `_WARNED_CIDRS`), never silently mistrusts good traffic.

    Catastrophic CIDRs (`0.0.0.0/0`, `::/0`, anything containing
    public addresses) are also dropped with a separate warning so
    the operator knows a misconfig was rejected, not silently
    honored. See `_is_safe_proxy_network`.
    """
    raw = os.environ.get(_TRUSTED_PROXIES_ENV, "").strip()
    if not raw:
        return ()
    nets: list = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            net = _ipaddress.ip_network(chunk, strict=False)
        except ValueError:
            _warn_bad_cidr_once(
                f"invalid:{chunk}",
                "BPP_TRUSTED_PROXIES: ignoring invalid CIDR %r",
                chunk,
            )
            continue
        if not _is_safe_proxy_network(net):
            _warn_bad_cidr_once(
                f"unsafe:{chunk}",
                "BPP_TRUSTED_PROXIES: rejecting unsafe CIDR %r — only "
                "loopback / RFC1918 private / link-local ranges may be "
                "trusted as proxy peers (got %s/%d)",
                chunk,
                net.network_address,
                net.prefixlen,
            )
            continue
        nets.append(net)
    return tuple(nets)


def _is_trusted_peer(remote: str) -> bool:
    """True if `remote` is inside an operator-declared trusted CIDR.

    Used by the LAN gate to decide whether the immediate peer is a
    known proxy that may forward identity. Returns False on empty
    config (default), unparseable IPs, or IPs outside every CIDR.
    """
    nets = _trusted_peer_networks()
    if not nets:
        return False
    try:
        addr = _ipaddress.ip_address(remote)
    except ValueError:
        return False
    return any(addr in n for n in nets)


def warn_if_legacy_trust_proxy() -> None:
    """Emit a one-shot startup warning if the deprecated flag is set.

    Called from `create_app()`. The legacy `BPP_TRUST_PROXY=1` is
    intentionally NOT honored — silently bypassing it would maintain
    the old blast-radius bug. The warning tells the operator to
    migrate to `BPP_TRUSTED_PROXIES=<cidr-list>`.
    """
    if os.environ.get(_LEGACY_TRUST_PROXY_ENV, "").lower() in ("1", "true", "yes"):
        log.warning(
            "BPP_TRUST_PROXY is deprecated and ignored. Migrate to "
            "BPP_TRUSTED_PROXIES=<cidr,cidr,...> with the explicit "
            "Docker-bridge / proxy CIDRs you trust. Default Docker: "
            "BPP_TRUSTED_PROXIES=172.16.0.0/12,192.168.65.0/24 — and "
            "publish the container with `-p 127.0.0.1:5001:5001`, NOT "
            "`-p 0.0.0.0:5001:5001`."
        )


def _reset_warned_cidrs_for_tests() -> None:
    """Test hook — reset the once-per-startup warning memo."""
    with _WARNED_CIDRS_LOCK:
        _WARNED_CIDRS.clear()
