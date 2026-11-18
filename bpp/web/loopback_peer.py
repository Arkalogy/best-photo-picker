"""Which account made a loopback connection (Linux).

Every account on the computer can connect to 127.0.0.1. On Linux the
launch command's arguments, boot key included, can be read by other
accounts while the browser starts, so the key alone does not show that
the caller is the account running the server. The kernel's socket table
does: ``/proc/net/tcp`` lists each TCP socket with the uid that owns it.

The answer is "another account" only when the table shows it. It is
"not known to be" when there is no table (other systems), when the
connection is not listed (a server inside a container never sees the
host's sockets), when the socket belongs to root (a system relay, as
under WSL, or a root that could read everything anyway), or when this
server itself runs as root.
"""

from __future__ import annotations

import ipaddress
import os
import re
import sys

_TABLES = ("net/tcp", "net/tcp6")
_WORDS = re.compile(r"[0-9A-Fa-f]{8}")


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An address as REMOTE_ADDR gives it; IPv4-mapped IPv6 as IPv4."""
    try:
        addr = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped or addr


def _table_address(hex_text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An address as the socket table prints it: each 32-bit word of the
    network-order address, shown as a number in this machine's byte order."""
    words = _WORDS.findall(hex_text)
    if len(words) * 8 != len(hex_text) or len(words) not in (1, 4):
        return None
    raw = b"".join(int(word, 16).to_bytes(4, sys.byteorder) for word in words)
    return _address(str(ipaddress.ip_address(raw)))


def _owner_uids(table: str, client: tuple[object, int], server_port: int) -> set[int]:
    """The uids owning sockets whose local end is *client* (address,
    port) and whose remote port is the server's."""
    uids: set[int] = set()
    try:
        with open(table, encoding="ascii", errors="replace") as f:
            next(f, None)  # header
            for line in f:
                parts = line.split()
                if len(parts) < 8:
                    continue
                local_addr, _, local_port = parts[1].rpartition(":")
                remote_port = parts[2].rpartition(":")[2]
                try:
                    if int(local_port, 16) != client[1] or int(remote_port, 16) != server_port:
                        continue
                    if _table_address(local_addr) != client[0]:
                        continue
                    uids.add(int(parts[7]))
                except ValueError:
                    continue
    except OSError:
        pass
    return uids


def peer_is_another_account(
    client_addr: str | None,
    client_port: int | None,
    server_port: int | None,
    *,
    proc_root: str = "/proc",
) -> bool:
    """True when the socket table shows the connection from
    (*client_addr*, *client_port*) to our port was made by an account
    other than the one running this server (root aside). False when it
    is ours, or when that can't be told (see the module docstring)."""
    if not client_port or not server_port or not hasattr(os, "geteuid"):
        return False
    me = os.geteuid()
    address = _address(client_addr or "")
    if me == 0 or address is None:
        return False
    uids: set[int] = set()
    for name in _TABLES:
        uids |= _owner_uids(os.path.join(proc_root, name), (address, client_port), server_port)
    # Every listed owner must be us (or root): a row of ours next to a
    # stranger's is not proof that the stranger is us.
    return bool(uids - {me, 0})
