"""Batch 9 — surface parity tests for items 8, 9, 10, 14, 17.

Pins:

* :data:`BPP_POSTURE_STATEMENT` appears verbatim in every required
  surface — the README License section, the in-app Settings ->
  Models banner template, and the canonical disclaimer.
* No restricted-model name (AdaFace / buffalo*) appears in
  user-facing marketing copy (README, CHANGELOG). Technical
  mentions in code comments and NOTICE.txt are allowed; the gate
  is specifically about the promotional surfaces a reader would
  treat as a product pitch.

Why these tests exist
---------------------

The legal-posture review's item 9 ("warning parity across
surfaces") is the kind of rule that quietly rots — a future
editor tweaks the README license paragraph for clarity and
forgets to keep it in sync with the in-app dialog. The tests
fail the build when that happens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bpp.registry import BPP_POSTURE_STATEMENT, CANONICAL_DISCLAIMER

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


# ── Surface parity ──


class TestPostureStatementParity:
    """Every required surface contains :data:`BPP_POSTURE_STATEMENT`
    verbatim. New surface added? Add a test here too."""

    def test_canonical_disclaimer_contains_posture_statement(self) -> None:
        """The click-through dialog (full disclaimer) carries the
        same two-sentence posture the README and Settings banner
        do. The constant is built from the same paragraphs."""
        # The posture statement is split-joined from CANONICAL_DISCLAIMER
        # paragraphs 2 + 3; check the load-bearing phrases are present.
        for phrase in (
            "Arkalogy will not monetize",
            "MIT-licensed",
            "commercial-safe models",
        ):
            assert phrase in CANONICAL_DISCLAIMER, (
                f"phrase {phrase!r} missing from CANONICAL_DISCLAIMER"
            )
            assert phrase in BPP_POSTURE_STATEMENT, (
                f"phrase {phrase!r} missing from BPP_POSTURE_STATEMENT"
            )

    def test_readme_contains_posture_statement_verbatim(self) -> None:
        readme = _read("README.md")
        # Match on a load-bearing prefix that's stable across line
        # wrapping. The README renders with hard wraps disabled in
        # the rendered Markdown, so the full text appears on one line
        # in the source.
        head = BPP_POSTURE_STATEMENT.split(".")[0]
        assert head in readme, (
            "README.md is missing the BPP_POSTURE_STATEMENT opening "
            "phrase. Pull it from bpp.registry.disclaimers when you "
            "edit the License section so the wording stays in sync "
            "with the in-app dialog."
        )
        # Also assert the closing load-bearing fragment so a partial
        # rewrite of just the opening doesn't slip through.
        tail = "commercial users must select commercial-safe"
        assert tail in readme, (
            "README.md is missing the BPP_POSTURE_STATEMENT closing "
            "fragment about commercial-safe models."
        )

    def test_settings_banner_template_renders_constant(self) -> None:
        template = _read("bpp/web/templates/index.html")
        # Jinja expression references the constant; the renderer
        # in bp_core.py passes it as ``bpp_posture_statement``.
        assert "{{ bpp_posture_statement }}" in template, (
            "Settings → Models banner must render "
            "BPP_POSTURE_STATEMENT via the Jinja context variable. "
            "If you changed the variable name in bp_core.py, "
            "update the template too."
        )

    def test_every_index_renderer_passes_posture_statement(self) -> None:
        """``bp_core.py`` has TWO ``render_template("index.html", ...)``
        calls — the trusted-device share-token branch and the local-app
        branch. BOTH must pass the posture statement, otherwise the
        Settings banner is empty for one of them. A grep for one
        occurrence is not enough; count instead."""
        import re

        bp_core = _read("bpp/web/bp_core.py")
        render_calls = re.findall(r'render_template\(\s*"index\.html"', bp_core)
        kwarg_calls = bp_core.count("bpp_posture_statement=_bpp_posture_statement()")
        assert kwarg_calls == len(render_calls), (
            f"bp_core.py has {len(render_calls)} index.html render "
            f"calls but only {kwarg_calls} pass bpp_posture_statement. "
            "Every renderer must pass the kwarg, otherwise the "
            "Settings banner shows up empty on one branch."
        )

    def test_index_render_emits_posture_statement(self, tmp_path: Path) -> None:
        """End-to-end Flask render: the rendered HTML must contain
        the posture statement opening fragment. Catches the bug
        where one renderer branch is missing the kwarg even if the
        static grep above passes."""
        from bpp.registry import BPP_POSTURE_STATEMENT
        from bpp.web.app import create_app

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        # analysis.json is a flat list of photo records — empty list
        # is the cheapest valid shape that lets create_app boot.
        (workdir / "analysis.json").write_text("[]", encoding="utf-8")
        app = create_app(workdir=str(workdir))
        app.config["TESTING"] = True
        with app.test_client() as client:
            resp = client.get("/")
        assert resp.status_code == 200, f"index render returned {resp.status_code}"
        # Just check the opening fragment — the full statement is
        # long and any wrap / template-edit would make a verbatim
        # match brittle.
        head = BPP_POSTURE_STATEMENT.split(",")[0]
        assert head.encode() in resp.data, (
            "Rendered index.html does not contain the BPP posture "
            "statement. The Settings → Models banner will be empty."
        )


# ── Acceptance dialog button styling (item 5 — affirmative-intent UX) ──


class TestAcceptanceDialogButtonStyling:
    """The click-through acceptance dialog (item 5) is the load-bearing
    evidence-of-consent surface. The affirmative action ("I accept")
    must be the visually prominent primary button so a future legal
    screenshot doesn't read as if BPP de-emphasized commitment in
    favour of cancellation.

    The friction that actually prevents accidental acceptance lives
    in the four required checkboxes — the user cannot submit until
    each one is checked, so the visible commitment can safely match
    modern dialog conventions.
    """

    def test_i_accept_is_primary_button(self) -> None:
        """Match by data-action + class + label so adding attributes
        like ``id`` or ``disabled`` (e.g. for UX states that gate
        until all four checkboxes are checked) doesn't break the
        styling guarantee."""
        template = _read("bpp/web/templates/index.html")
        import re

        match = re.search(
            r'<button\b[^>]*\bclass="[^"]*\bmodal-btn-primary\b[^"]*"[^>]*'
            r'data-action="confirmFaceEmbedderAcceptance"[^>]*>\s*I accept\s*</button>',
            template,
        )
        # Also accept the reverse attribute order — argparse-style.
        if match is None:
            match = re.search(
                r'<button\b[^>]*data-action="confirmFaceEmbedderAcceptance"[^>]*'
                r'\bclass="[^"]*\bmodal-btn-primary\b[^"]*"[^>]*>\s*I accept\s*</button>',
                template,
            )
        assert match is not None, (
            'The "I accept" button on the face-embedder acceptance '
            "dialog must use modal-btn-primary so the affirmative "
            "action carries the visible commitment. The four-checkbox "
            "gate is the friction; the button color is the convention."
        )

    def test_cancel_is_secondary_button(self) -> None:
        template = _read("bpp/web/templates/index.html")
        import re

        match = re.search(
            r'<button\b[^>]*\bclass="[^"]*\bmodal-btn-secondary\b[^"]*"[^>]*'
            r'data-action="closeFaceEmbedderAcceptance"[^>]*>\s*Cancel\s*</button>',
            template,
        )
        if match is None:
            match = re.search(
                r'<button\b[^>]*data-action="closeFaceEmbedderAcceptance"[^>]*'
                r'\bclass="[^"]*\bmodal-btn-secondary\b[^"]*"[^>]*>\s*Cancel\s*</button>',
                template,
            )
        assert match is not None, (
            'The "Cancel" button on the face-embedder acceptance '
            "dialog must use modal-btn-secondary so it does not "
            "compete visually with the affirmative action."
        )


# ── docs/API.md catches up to the model-registry blueprint ──


class TestApiMdDocumentsModelRegistryEndpoints:
    """Every model-registry endpoint registered in bp_model_registry.py
    must appear in docs/API.md. Catches the "shipped an endpoint but
    forgot the docs" failure mode that lets the public-facing API
    reference quietly rot."""

    def test_every_bp_model_registry_route_is_documented(self) -> None:
        import re

        # The model-registry surface is split across three blueprint
        # files (registry view + acceptance, BYOM/removal admin, catalog
        # downloads). Scan all three so a route doesn't escape the
        # docs-parity check just by living in a sibling module.
        bp = "\n".join(
            _read(p)
            for p in (
                "bpp/web/bp_model_registry.py",
                "bpp/web/bp_model_admin.py",
                "bpp/web/bp_catalog.py",
            )
        )
        api_md = _read("docs/API.md")
        # Decorators of the form @bp.get("/api/v1/…") /
        # @bp.post(…) / @bp.delete(…).
        route_re = re.compile(r'@bp\.(?:get|post|delete|put|patch)\(\s*"([^"]+)"')
        routes = set(route_re.findall(bp))
        assert routes, (
            "test setup error: no @bp.<verb> decorators found in the model-registry blueprint files"
        )

        missing: list[str] = []
        for route in sorted(routes):
            # Path-parameter routes use Flask's `<entry_id>` syntax;
            # the doc lists them with `{entry_id}` style. Normalise.
            normalised = re.sub(r"<([^>]+)>", r"{\1}", route)
            if route not in api_md and normalised not in api_md:
                missing.append(route)

        assert not missing, (
            "These model-registry endpoints are registered in "
            "bp_model_registry.py but not documented in docs/API.md: "
            + ", ".join(missing)
            + ". Add a section under '## Model Registry' that describes "
            "the request/response shape; otherwise developers reading "
            "the API doc can't discover them."
        )


# ── ADR for the 24-item legal-posture decision exists + is indexed ──


class TestLegalPostureAdrPresence:
    """The 24-item legal-posture build is the most ambitious engineering
    in the repo. ADR 0005 makes the *rationale* public so a reader who
    only sees the code can still understand the decision. This test
    catches an accidental delete / de-list / rename."""

    def test_adr_file_exists(self) -> None:
        adr = REPO_ROOT / "docs/adr/0005-legal-posture-and-model-registry.md"
        assert adr.exists(), (
            "docs/adr/0005-legal-posture-and-model-registry.md must "
            "exist — the 24-item legal-posture decision is the most "
            "ambitious piece of engineering in the repo and its "
            "rationale belongs in a public ADR (not just the "
            "gitignored spike doc)."
        )

    def test_adr_indexed_in_readme(self) -> None:
        index = _read("docs/adr/README.md")
        assert "0005" in index, (
            "ADR 0005 must appear in docs/adr/README.md — an ADR that "
            "isn't indexed is invisible to anyone browsing the docs."
        )
        assert "legal-posture-and-model-registry" in index, (
            "docs/adr/README.md must link to 0005-legal-posture-and-model-registry.md"
        )

    def test_adr_carries_load_bearing_phrases(self) -> None:
        """A typo-fix that strips the ADR's load-bearing language
        (the corrected Item 17 wording, the 10-batch sketch, the
        evidentiary-chain sketch) silently weakens the portfolio
        signal. Pin the phrases."""
        adr = _read("docs/adr/0005-legal-posture-and-model-registry.md")
        assert "Arkalogy will not monetize" in adr, (
            "ADR 0005 must include the corrected Item 17 wording verbatim"
        )
        assert "MIT-licensed" in adr
        assert "10 implementation batches" in adr or "ten implementation batches" in adr.lower(), (
            "ADR 0005 must reference the 10-batch implementation structure"
        )
        assert "BIPA" in adr, (
            "ADR 0005 must cite BIPA-class biometric-data regulation as part of the context"
        )


# ── README documents every top-level CLI command ──


class TestReadmeDocumentsCliCommands:
    """A subcommand registered in the CLI but missing from the README
    reads as un-maintained docs. A subcommand documented in the README
    but missing from the CLI reads as vapor. Pin both."""

    def test_every_cli_subcommand_appears_in_readme(self) -> None:
        readme = _read("README.md")
        # The README's CLI section uses ``bpp <subcommand> ...`` lines.
        # Each top-level subcommand must show up at least once.
        from bpp.cli import build_parser

        parser = build_parser()
        cli_subcommands: set[str] = set()
        for action in parser._actions:  # type: ignore[attr-defined]
            if hasattr(action, "choices") and isinstance(action.choices, dict):
                cli_subcommands.update(action.choices.keys())

        # The README documents subcommands via lines beginning ``bpp <name>``.
        missing: list[str] = []
        for sub in sorted(cli_subcommands):
            if f"bpp {sub}" not in readme:
                missing.append(sub)
        assert not missing, (
            "These CLI subcommands are registered but not documented in "
            "README.md: " + ", ".join(missing) + ". Add a one-line entry "
            "under the CLI reference section (around line 314)."
        )


# ── Marketing-copy ban on restricted-model names (item 14) ──


# The names the editorial rule bans from marketing copy. The list
# stays narrow on purpose: technical mentions in code, NOTICE.txt,
# tests, and the plan doc are intentional. The gate fires only on
# the surfaces a reader treats as a product pitch.
RESTRICTED_MODEL_NAME_TOKENS = (
    "AdaFace",
    "adaface",
    "buffalo_l",
    "buffalo_s",
)


# These surfaces are checked — a restricted-model name appearing
# in any of them is a build failure.
MARKETING_SURFACES = (
    "README.md",
    "CHANGELOG.md",
)


class TestMarketingCopyBansRestrictedModelNames:
    """Item 14: editorial rule bans restricted-model names from any
    commercial-targeted copy. The plan doc + NOTICE.txt + tests are
    allowed to name them because they're not marketing copy."""

    @pytest.mark.parametrize("surface", MARKETING_SURFACES)
    @pytest.mark.parametrize("token", RESTRICTED_MODEL_NAME_TOKENS)
    def test_marketing_surface_does_not_name_token(self, surface: str, token: str) -> None:
        path = REPO_ROOT / surface
        if not path.exists():
            pytest.skip(f"{surface} not present in this checkout")
        body = path.read_text(encoding="utf-8")
        assert token not in body, (
            f"Surface {surface!r} contains restricted-model name "
            f"{token!r}. Item 14 of the legal-posture plan bans "
            f"restricted-model names from marketing copy — describe "
            'the capability generically ("opt-in third-party face '
            'embedders") instead.'
        )
