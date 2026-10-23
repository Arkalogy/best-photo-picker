"""The app answers only to its own addresses.

A web page the owner has open can rebind its own domain to 127.0.0.1
(DNS rebinding) and then read ``/`` as a same-origin request, including
the session token rendered into it. The browser sends that page's domain
as the Host header; a person who typed the app's address sends
``localhost``, an IP literal, or a name the operator chose. Refusing
every other Host before authorization runs closes the hole.

``BPP_TRUSTED_HOSTS`` (comma-separated) adds names for reverse-proxy
deployments; a leading dot covers subdomains (``.example.net`` allows
``example.net`` and ``photos.example.net``).
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable

ENV_TRUSTED_HOSTS = "BPP_TRUSTED_HOSTS"


def _hostname(host: str) -> str | None:
    """The host part of a Host header, lower-cased, or None when malformed."""
    h = host.strip().lower()
    if not h or any(c.isspace() for c in h):
        return None
    if h.startswith("["):  # IPv6 literal, optionally with a port
        end = h.find("]")
        if end == -1:
            return None
        rest = h[end + 1 :]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return None
        return h[1:end] or None
    if h.count(":") > 1:  # a bare IPv6 literal must be bracketed in a Host header
        return None
    name, _, port = h.partition(":")
    if port and not port.isdigit():
        return None
    return name or None


def host_is_allowed(host: str, extra: Iterable[str]) -> bool:
    """True when ``host`` is loopback by name, an IP literal, or in ``extra``."""
    name = _hostname(host)
    if name is None:
        return False
    if name == "localhost":
        return True
    try:
        ipaddress.ip_address(name.replace("%25", "%").split("%", 1)[0])
        return True
    except ValueError:
        pass
    for entry in extra:
        e = entry.strip().lower()
        if not e:
            continue
        if e.startswith("."):
            if name == e[1:] or name.endswith(e):
                return True
        elif name == e:
            return True
    return False


def configured_hosts() -> tuple[str, ...]:
    """Operator-added hosts from ``BPP_TRUSTED_HOSTS``."""
    raw = os.environ.get(ENV_TRUSTED_HOSTS, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())
