"""``docs/API.md`` names the install fields of ``/api/v1/status``, the
``/api/v1/install/state`` endpoint, and the health blocks.

The install state grew five status fields and its own endpoint without
the doc following. The keys come from the code that builds them, so a
new key fails here until the doc names it.
"""

from __future__ import annotations

import re

from tests.test_api_doc_response_fields import _section


def _doc_keys(section: str) -> set[str]:
    return set(re.findall(r'^\s*"([a-z_]+)":', section, re.MULTILINE))


def test_status_documents_every_install_field():
    from bpp.web.install_status import install_status_fields

    documented = _doc_keys(_section("### GET /api/v1/status"))
    missing = set(install_status_fields(owner=True)) - documented
    assert not missing, f"docs/API.md GET /api/v1/status lacks {sorted(missing)}"


def test_install_state_endpoint_is_documented_with_its_fields():
    from bpp.web.install_status import install_state

    documented = _doc_keys(_section("### GET /api/v1/install/state"))
    missing = set(install_state(owner=True)) - documented
    assert not missing, f"docs/API.md GET /api/v1/install/state lacks {sorted(missing)}"


def test_health_documents_the_install_download_and_phash_blocks():
    documented = _doc_keys(_section("### GET /api/v1/health"))
    assert {"install", "downloads", "phash"} <= documented
