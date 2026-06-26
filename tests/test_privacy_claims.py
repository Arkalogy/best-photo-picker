"""Privacy-claims regression: code never adds an undocumented egress.

README's privacy section + SECURITY.md's "External network calls"
list enumerate every host the running app can reach. Three drift
cases survived past commits because no test enforced the
correspondence:

  * Nominatim was claimed for reverse geocoding but never coded.
  * Runtime PyPI installs (Settings → Advanced → ML Models)
    weren't disclosed.
  * Map tiles fired from the lightbox too, not only the Map view.

This test reads the code's actual URL literals and asserts each
network destination appears in a hand-maintained allowlist below,
which mirrors the docs. New code that adds a network call without
updating docs fails here. New docs that strike a still-active
host also fail here. README/SECURITY then become an executable
contract, not just prose.

The allowlist is intentionally small and explicit. Adding a new
entry should require touching the docs in the same commit.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Allowlist of network destinations we publicly claim to use ────
#
# Each row: (host_pattern, kind, doc_anchor) where doc_anchor is a
# substring that must appear in README.md or SECURITY.md so the
# claim is anchored to user-facing prose. host_pattern matches the
# `host` portion of an extracted URL (substring-match — wildcard
# subdomains expressed as bare domain).
_ALLOWLIST = (
    # Model downloads (first-run per feature) — README "Model
    # downloads" bullet, SECURITY threat-model summary.
    ("media.githubusercontent.com", "model-download", "Model downloads"),
    ("huggingface.co", "model-download", "Model downloads"),
    ("storage.googleapis.com", "model-download", "Model downloads"),
    ("github.com/ultralytics", "model-download", "Model downloads"),
    ("openaipublic.azureedge.net", "model-download", "Model downloads"),
    # Update check (background; toggleable) — README "Update check"
    # bullet.
    ("api.github.com", "update-check", "Update check"),
    # Runtime PyPI installs (Settings → Advanced → ML Models;
    # explicit click) — README "Runtime dependency installs" bullet.
    # No literal URL in source — pip subprocess takes the host
    # implicitly. Documented but not fingerprintable here, so this
    # entry is informational.
    # Map tiles (Map view + lightbox) — README "Map tiles" bullet.
    ("tile.openstreetmap.org", "map-tile", "Map tiles"),
    # Attribution link rendered in map UI — not a fetch from JS,
    # but appears in source as a string and would otherwise trip
    # the host extractor.
    ("www.openstreetmap.org/copyright", "attribution-link", "Map tiles"),
)

# Hosts that show up in source but are NOT network calls from the
# running app. Listed explicitly so the test can distinguish them
# from real egress and the next contributor knows why each is here.
_NON_FETCH_HOSTS = frozenset(
    {
        # Loopback + local — never leave the host
        "127.0.0.1",
        "localhost",
        # XML namespace identifiers in EXIF/XMP serialization (RDF
        # convention is to use a URL string as a namespace; no
        # fetch happens)
        "www.w3.org",
        "ns.adobe.com",
        # Footer hrefs (user-click only, not auto-fetch)
        "arkalogy.com",
        "buymeacoffee.com",
        # Docstring examples / placeholder strings
        "...",
    }
)

# Source files we scan. Listed explicitly rather than auto-globbing
# so a new file that adds a network call gets a code-review
# moment when it's added to this list.
_SOURCE_FILES = (
    # Python: model downloaders + update checker + share-URL builder
    REPO_ROOT / "bpp" / "scoring" / "pets.py",
    REPO_ROOT / "bpp" / "scoring" / "face.py",
    REPO_ROOT / "bpp" / "scoring" / "face_blazeface_fr.py",
    REPO_ROOT / "bpp" / "scoring" / "face_embed.py",
    REPO_ROOT / "bpp" / "scoring" / "face_expression.py",
    REPO_ROOT / "bpp" / "scoring" / "face_hand_filter.py",
    REPO_ROOT / "bpp" / "scoring" / "face_scrfd.py",
    REPO_ROOT / "bpp" / "scoring" / "pose.py",
    REPO_ROOT / "bpp" / "scoring" / "segmentation.py",
    REPO_ROOT / "bpp" / "scoring" / "clip_embed.py",
    REPO_ROOT / "bpp" / "scoring" / "clip_tokenizer.py",
    REPO_ROOT / "bpp" / "web" / "update_checker.py",
    # JS: Map + lightbox tile layers
    REPO_ROOT / "bpp" / "web" / "static" / "js" / "modules" / "map.mjs",
    REPO_ROOT / "bpp" / "web" / "static" / "js" / "modules" / "lightbox.mjs",
)

_URL_RE = re.compile(r"https?://([A-Za-z0-9.\-]+)(/[^\s\"')\}]*)?")


def _extract_urls() -> list[tuple[str, str, str, int]]:
    """Return (host, host_plus_path, source_file, line_number) for
    every URL literal found in the scanned source files. Reporting
    keys on the bare host but matching uses the path-aware form so
    allowlist entries like `github.com/ultralytics` can scope to a
    single org without trusting all of github.com."""
    hits: list[tuple[str, str, str, int]] = []
    for path in _SOURCE_FILES:
        if not path.exists():
            continue
        rel = str(path.relative_to(REPO_ROOT))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in _URL_RE.finditer(line):
                host = m.group(1)
                full = host + (m.group(2) or "")
                hits.append((host, full, rel, lineno))
    return hits


def _is_allowed(host: str, full: str) -> bool:
    """An entry is allowed when its bare host is in _NON_FETCH_HOSTS,
    OR an allowlist pattern matches against either the bare host or
    the full host+path. Path-prefix patterns (`github.com/ultralytics`)
    deliberately do NOT match against bare host alone — that's the
    point of including the path prefix."""
    if host in _NON_FETCH_HOSTS:
        return True
    for pattern, _kind, _anchor in _ALLOWLIST:
        if "/" in pattern:
            # Path-aware pattern — must match the full URL
            if pattern in full:
                return True
        else:
            # Host-only pattern — match against the bare host
            if pattern in host:
                return True
    return False


def test_no_undocumented_egress_in_source():
    """Every URL host in the scanned source files must be either
    a non-fetch (docstring / namespace / loopback) or in the
    documented allowlist. New egress hosts MUST be added here AND
    to README/SECURITY in the same commit."""
    hits = _extract_urls()

    # Group by host so we report the offender once with all sites.
    by_host: dict[str, list[str]] = {}
    for host, full, source, lineno in hits:
        if not _is_allowed(host, full):
            by_host.setdefault(host, []).append(f"{source}:{lineno} ({full})")

    if by_host:
        msg_parts = ["Undocumented network destinations found in source:"]
        for host, sites in sorted(by_host.items()):
            msg_parts.append(f"  {host}")
            for s in sites:
                msg_parts.append(f"    at {s}")
        msg_parts.append(
            "\nIf this is a legitimate new egress, add the host to "
            "_ALLOWLIST in tests/test_privacy_claims.py AND to README's "
            "Privacy section + SECURITY.md's network bullet in the "
            "SAME commit. If it's a false positive (docstring example, "
            "XML namespace, loopback), add the host to _NON_FETCH_HOSTS."
        )
        raise AssertionError("\n".join(msg_parts))


def test_each_allowlisted_host_is_anchored_in_docs():
    """Every entry in _ALLOWLIST must have its `doc_anchor` substring
    in README.md or SECURITY.md. Catches the inverse drift: a doc
    edit that strikes the disclosure for a still-active call."""
    readme = (REPO_ROOT / "README.md").read_text()
    security = (REPO_ROOT / "SECURITY.md").read_text()
    haystack = readme + security

    missing = []
    for host, kind, anchor in _ALLOWLIST:
        if anchor not in haystack:
            missing.append(f"{host} ({kind}) — anchor {anchor!r} not in README or SECURITY")

    assert not missing, (
        "Allowlist entries lack doc anchors — README/SECURITY may "
        "have been edited without updating the privacy claim list:\n  " + "\n  ".join(missing)
    )


def test_pypi_runtime_installs_are_documented():
    """Runtime PyPI installs are an indirect egress (subprocess pip)
    so they don't fingerprint as a URL literal. Spot-check that the
    disclosure is still in README and SECURITY by name."""
    readme = (REPO_ROOT / "README.md").read_text()
    security = (REPO_ROOT / "SECURITY.md").read_text()
    for doc_name, text in (("README.md", readme), ("SECURITY.md", security)):
        assert "PyPI" in text or "pip install" in text.lower(), (
            f"{doc_name} no longer mentions PyPI/pip-install — runtime "
            "dependency installs are an undisclosed network destination"
        )
