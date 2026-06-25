# Vendored third-party assets

Map-view dependencies vendored locally so the app shell makes no
third-party network call on page load. (Map *tiles* are fetched
from OpenStreetMap when the user opens the Map view — that's a
deliberate, scoped network call documented in the README.)

| Path | Project | Version | License | License file |
|------|---------|---------|---------|--------------|
| `leaflet/` | [Leaflet](https://leafletjs.com/) | 1.9.4 | BSD-2-Clause | [LICENSE](leaflet/LICENSE) |
| `leaflet.markercluster/` | [Leaflet.markercluster](https://github.com/Leaflet/Leaflet.markercluster) | 1.5.3 | MIT | [MIT-LICENCE.txt](leaflet.markercluster/MIT-LICENCE.txt) |

Both are permissive (BSD-2-Clause / MIT) and compatible with
this project's MIT license. The upstream license text ships
alongside each vendored copy as required by their redistribution
clauses.

## Why vendored, not pinned via CDN

A CDN script tag in `index.html` is a network call on every page
load. The README claims "local-first, opt-in network features
only" — a `unpkg.com` fetch on first paint contradicts that. CDN
risks include outage, version pin drift, supply-chain compromise,
and accidental telemetry (CDN access logs).

## How to update

1. Download the new versions from each project's release page or
   `https://unpkg.com/<package>@<version>/dist/...`.
2. Replace files in-place. Match the directory layout — Leaflet's
   CSS references `images/marker-icon.png` etc. with relative
   paths, so the directory shape matters.
3. Update the version numbers in this file and in
   `bpp/web/templates/index.html`.
4. Run `npm run test:js` and `pytest tests/test_no_third_party_cdn.py`.
