"""Content-Security-Policy and cache-control regression tests.

CSP contract:
- ``script-src 'self' 'nonce-...'`` — nonce-based, no unsafe-inline.
  All inline event handlers have been migrated to data-action /
  data-oninput attributes dispatched by globals.js.
- ``style-src 'self' 'unsafe-inline'`` — still needed for inline
  <style> blocks and dynamic ``style=`` attributes set by JS.
- No external script sources; Leaflet/markercluster are vendored.

JS cache contract:
- ``/static/js/`` responses carry ``Cache-Control: no-store`` to
  prevent WKWebView serving stale JS from disk cache after an update.
"""

from __future__ import annotations

import os
import re

import pytest

from bpp.web.app import create_app

# Inline event handler attributes we know the SPA uses. Used by the
# core invariant test below — if ANY of these appear in the served
# HTML, the CSP must allow them (no nonce in script-src).
_INLINE_HANDLER_ATTRS = (
    "onclick",
    "oninput",
    "onchange",
    "onsubmit",
    "onkeydown",
    "onkeyup",
    "oncontextmenu",
    "onmouseenter",
    "onmouseleave",
    "onmousedown",
    "onmouseup",
    "onload",
    "onerror",
    "onfocus",
    "onblur",
)


@pytest.fixture()
def app(tmp_path):
    workdir = str(tmp_path / "wd")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False
    return app


def _fetch_index(app):
    """Return (status, headers, body) for GET / as a loopback owner."""
    client = app.test_client()
    ctx = app.extensions["bpp"]
    r = client.get(
        "/",
        headers={"X-Auth-Token": ctx.auth_token},
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )
    return r.status_code, r.headers, r.get_data(as_text=True)


def _script_src(csp_header: str) -> str:
    """Extract the script-src directive value from a CSP header."""
    m = re.search(r"script-src\s+([^;]+)", csp_header)
    if not m:
        raise AssertionError(f"No script-src directive in CSP: {csp_header}")
    return m.group(1).strip()


def test_html_response_has_csp_header(app):
    status, headers, _ = _fetch_index(app)
    assert status == 200
    assert "Content-Security-Policy" in headers


def test_script_src_no_unsafe_inline(app):
    """After the onclick→data-action migration, script-src must NOT
    contain 'unsafe-inline'. Inline event handlers no longer exist."""
    _, headers, _ = _fetch_index(app)
    script_src = _script_src(headers["Content-Security-Policy"])
    assert "'unsafe-inline'" not in script_src, (
        "script-src still contains 'unsafe-inline'. The onclick→data-action "
        "migration is complete — remove 'unsafe-inline' from script-src in "
        "bpp/web/app.py."
    )
    assert "'self'" in script_src


def test_script_src_has_no_nonce_while_inline_handlers_exist(app):
    """**The bug-of-the-decade test.**

    If the served HTML contains ANY inline event handler attribute
    (onclick, oninput, oncontextmenu, etc.), the CSP script-src
    directive MUST NOT contain a `'nonce-...'` source.

    Per CSP3, when both a nonce and 'unsafe-inline' are present,
    the nonce wins and 'unsafe-inline' is ignored — including for
    event handler attributes, which cannot carry a nonce. The
    result is silent: no console error visible to the end user,
    just clicks that do nothing.

    If this test ever fails, the choice is:
    1. Migrate every inline handler in the offending elements to
       addEventListener / event delegation, OR
    2. Drop the nonce-source from script-src in `bpp/web/app.py`.

    Don't paper over by adding 'unsafe-hashes' — browser support
    is uneven and you'd have to hash every handler string."""
    _, headers, body = _fetch_index(app)
    script_src = _script_src(headers["Content-Security-Policy"])

    # Count raw inline handler attributes. Use negative lookbehind so
    # `data-onchange=` (dispatcher target) doesn't match — only bare
    # `onchange=` counts as an inline handler.
    handler_pattern = re.compile(
        r"(?<!-)(?<!\w)\b(" + "|".join(_INLINE_HANDLER_ATTRS) + r")\s*=",
        re.IGNORECASE,
    )
    handler_hits = handler_pattern.findall(body)

    if not handler_hits:
        # Migration completed — test no longer applies. Rename or
        # delete this test if/when that lands.
        return

    assert "'nonce-" not in script_src, (
        f"CSP script-src contains a nonce-source ({script_src!r}) but "
        f"the served HTML contains {len(handler_hits)} inline event "
        f"handler attribute(s) (e.g. {sorted(set(handler_hits))[:5]}). "
        "Per CSP3, the nonce overrides 'unsafe-inline' and silently "
        "blocks every one of those handlers. Either remove the nonce "
        "from script-src in bpp/web/app.py:_set_csp(), or migrate the "
        "handlers to addEventListener first."
    )


def test_no_inline_handlers_in_index(app):
    """After the onclick→data-action migration, the served index.html
    must contain zero raw onclick/oninput/etc. attributes.

    Uses a negative lookbehind to skip `data-onXxx=` attributes (which
    are the dispatcher bindings, not inline handlers)."""
    _, _, body = _fetch_index(app)
    # `(?<!-)` ensures we don't match `data-onchange=` etc.
    handler_pattern = re.compile(
        r"(?<!-)(?<!\w)\b(" + "|".join(_INLINE_HANDLER_ATTRS) + r")\s*=",
        re.IGNORECASE,
    )
    hits = handler_pattern.findall(body)
    assert not hits, (
        f"Served index.html still contains {len(hits)} inline event handler "
        f"attribute(s): {sorted(set(hits))}. The onclick→data-action migration "
        "should have removed all of them."
    )


def test_no_inline_handlers_in_js_templates(app):
    """Scan every JS module for inline event handler attributes inside
    template literals (innerHTML strings).

    The static-HTML test catches handlers in index.html but misses
    dynamically-generated card/chip HTML in *.mjs files. This test
    greps the source directly so it's fast and runs without a browser.

    Allowed: data-onXxx= (dispatcher bindings), JS property assignments
    like `el.onclick = ...` (not HTML attributes), and comments.

    Rejected: any bare onXxx= that would end up as an HTML attribute
    in a template string — these are silently blocked by the nonce CSP.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    js_dir = repo / "bpp" / "web" / "static" / "js" / "modules"

    # Match `onXxx="` or `onXxx='` that is NOT preceded by `data-`
    # and NOT on a line that starts with // (comment).
    attr_re = re.compile(
        r"(?<!-)(?<!\w)\b(on(?:click|change|input|keydown|keyup|contextmenu"
        r"|mousedown|mouseup|mouseleave|mouseenter|pointerdown|pointerup"
        r"|load|error|dblclick|toggle|touchstart|touchend))\s*=\s*[\"']",
        re.IGNORECASE,
    )

    hits: list[tuple[str, int, str]] = []
    for mjs in sorted(js_dir.glob("*.mjs")):
        for lineno, line in enumerate(mjs.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for _m in attr_re.finditer(line):
                hits.append((mjs.name, lineno, line.strip()[:120]))

    assert not hits, (
        f"Found {len(hits)} inline event handler attribute(s) in JS template strings "
        "(these are blocked by nonce CSP and silently break UI features):\n"
        + "\n".join(f"  {f}:{ln}  {txt}" for f, ln, txt in hits[:20])
    )


def test_data_action_targets_exist_on_window(app):
    """Every data-action="funcName" value in JS modules must correspond
    to a function exposed on window (listed in .eslint-globals.json).

    The dispatcher calls window[name]() — if the function isn't on window
    the click silently does nothing. This test catches that at source level.

    Exemptions:
    - kebab-case strings (e.g. "toggle-exclude") are context-menu item
      identifiers looked up via querySelector, not dispatcher targets.
    - Template-expression values (containing ${) are runtime-computed.
    - Functions defined as window.xxx = ... in globals.js are added to
      a supplemental set scanned directly from that file.
    """
    import json
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    js_dir = repo / "bpp" / "web" / "static" / "js"

    # Load ESLint globals (top-level function/var/const/let declarations)
    with open(repo / ".eslint-globals.json") as f:
        known = set(json.load(f).keys())

    # Also pick up window.xxx = ... assignments in globals.js
    window_assign_re = re.compile(r"window\.(\w+)\s*=")
    for m in window_assign_re.finditer((js_dir / "globals.js").read_text()):
        known.add(m.group(1))

    # Also pick up registerAction("name", ...) calls across modules.
    # registerAction routes data-action="name" clicks through the
    # action-registry dispatcher in addition to the legacy window
    # globals path, so a name registered this way is just as
    # reachable from a data-action handler.
    register_action_re = re.compile(r"""registerAction\(\s*['"]([^'"]+)['"]""")
    for path in (js_dir / "modules").glob("*.mjs"):
        for m in register_action_re.finditer(path.read_text(encoding="utf-8")):
            known.add(m.group(1))

    # Collect all static data-action values from JS modules + HTML
    action_re = re.compile(r'data-action=["\']([^"\'${}|]+)["\']')
    missing: list[tuple[str, int, str]] = []

    sources = list((js_dir / "modules").glob("*.mjs")) + list(
        (repo / "bpp" / "web" / "templates").glob("*.html")
    )
    # Context-menu item identifiers: these sit inside `.ctx-menu` elements
    # and are handled by dedicated ctx-menu click listeners (not the global
    # dispatcher, which now explicitly skips `.ctx-menu` elements).
    # The global dispatcher's skip-guard is in globals.js _bppDispatch.
    CTX_MENU_IDS = {
        "include",
        "exclude",
        "favorite",
        "edit",
        "enhance",
        "delete",
        "rename",
        "merge",
        "split",
        "dismiss",
        "identify",
        "restore",
        "unhide",
        "hide",
        "perm-delete",
        "not-a-face",
        "change-avatar",
        "merge-selected",
        "toggle-exclude",
        "reassign",
        "add-face",
        "sensitive",
    }

    for path in sorted(sources):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("//", "*")):
                continue
            for m in action_re.finditer(line):
                val = m.group(1).strip()
                # Skip kebab-case (context menu identifiers, not dispatcher targets)
                if "-" in val:
                    continue
                # Skip known ctx-menu item identifiers (handled directly, not via dispatcher)
                if val in CTX_MENU_IDS:
                    continue
                # Skip if not a valid JS identifier
                if not re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", val):
                    continue
                if val not in known:
                    missing.append((path.name, lineno, val))

    assert not missing, (
        f"Found {len(missing)} data-action value(s) not exposed on window "
        "(dispatcher will silently no-op at runtime):\n"
        + "\n".join(f'  {f}:{ln}  data-action="{v}"' for f, ln, v in missing[:20])
    )


class TestJSCacheHeaders:
    """Regression: /static/js/ must return Cache-Control: no-store.

    WKWebView (Tauri) caches static assets to disk and may serve stale
    JS after an app update. CSS is cache-busted via ?v= query params;
    ES modules resolve relative imports without the param, so the
    no-store header is the reliable strategy for JS.
    """

    @pytest.fixture()
    def client(self, tmp_path):
        import json
        import os

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        analysis: list = []
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        return app.test_client()

    def test_static_js_module_has_no_store(self, client):
        resp = client.get("/static/js/globals.js")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc, (
            f"Expected Cache-Control: no-store on /static/js/ response, got: {cc!r}"
        )

    def test_static_js_module_mjs_has_no_store(self, client):
        resp = client.get("/static/js/modules/app.mjs")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc, (
            f"Expected Cache-Control: no-store on /static/js/modules/ response, got: {cc!r}"
        )

    def test_static_css_not_affected(self, client):
        """CSS uses ?v= cache busting — no-store header should not be set."""
        resp = client.get("/static/css/app.css")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" not in cc, (
            f"CSS should not have no-store (uses ?v= cache busting instead), got: {cc!r}"
        )
