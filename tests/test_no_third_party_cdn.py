"""Regression: app shell must not fetch from third-party CDNs.

The README/SECURITY docs claim "local-first, opt-in network
features only". A `<script src="https://unpkg.com/...">` tag in
the app shell contradicts that — it's a network call on first
paint, before any user opt-in.

These tests ensure no template references the public CDN hosts
we previously used (unpkg, jsdelivr, cdnjs). New third-party
assets must be vendored under `bpp/web/static/vendor/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "bpp" / "web" / "templates"

# Hosts that previously appeared in templates. Keep this conservative
# — we'd rather miss a brand-new CDN host than block the test from
# matching at all.
_BANNED_HOSTS = ("unpkg.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com")


@pytest.mark.parametrize(
    "template",
    sorted(p for p in TEMPLATES_DIR.glob("*.html")),
    ids=lambda p: p.name,
)
def test_template_does_not_reference_third_party_cdn(template):
    text = template.read_text(encoding="utf-8")
    for host in _BANNED_HOSTS:
        assert host not in text, (
            f"{template.name} loads {host} — vendor it locally under "
            f"bpp/web/static/vendor/ instead. See "
            f"bpp/web/static/vendor/README.md for the rationale."
        )


def test_leaflet_vendored_assets_present():
    """If we drop the vendor copies, the static check above can't
    catch it (because the unpkg references are gone but the local
    files are missing too) — explicitly verify the files exist."""
    base = REPO_ROOT / "bpp" / "web" / "static" / "vendor"
    expected = [
        base / "leaflet" / "dist" / "leaflet.js",
        base / "leaflet" / "dist" / "leaflet.css",
        base / "leaflet" / "dist" / "images" / "marker-icon.png",
        base / "leaflet.markercluster" / "dist" / "leaflet.markercluster.js",
        base / "leaflet.markercluster" / "dist" / "MarkerCluster.css",
        base / "leaflet.markercluster" / "dist" / "MarkerCluster.Default.css",
    ]
    missing = [str(p.relative_to(REPO_ROOT)) for p in expected if not p.exists()]
    assert not missing, (
        "Vendored Leaflet assets are missing — restore them from the "
        f"original CDN URLs (see bpp/web/static/vendor/README.md): {missing}"
    )
