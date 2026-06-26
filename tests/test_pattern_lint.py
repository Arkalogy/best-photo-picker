"""Pattern-lint meta-tests: codify the recurring bug patterns that
audits keep surfacing so they fail at test-time, not 6 months from
now during a third /review pass.

Each test enforces ONE project invariant by scanning the source
tree. New violations fail CI; the test message points at the exact
file:line and the helper to use instead.

The patterns enforced here came directly out of two repeated full
audits — every rule below is something we either committed a fix
for, or could have prevented by codifying earlier. Conventional
ruff / mypy don't catch them; they're project-specific.

Conventions:
- Rules use either `ast` (for structural checks like decorators) or
  `re` (for substring patterns). Pick whichever is simplest for the
  invariant.
- Each rule has an explicit allowlist of paths where the pattern is
  legitimate (e.g. the file that DEFINES the helper everyone else
  must use).
- Failure messages must include file:line + the line of code + the
  helper to call instead. A failing CI run should not require
  re-running with `-v`.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BPP_DIR = REPO_ROOT / "bpp"
JS_MODULES_DIR = REPO_ROOT / "bpp" / "web" / "static" / "js" / "modules"


def _iter_py_files(root: Path, *, exclude: set[Path] | None = None):
    """Yield (path, source) for every .py file under *root*."""
    exclude = exclude or set()
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path in exclude:
            continue
        yield path, path.read_text()


# ---------------------------------------------------------------------------
# Rule 1: no raw `-1` / `-2` cluster sentinels in Python source
# ---------------------------------------------------------------------------


def _strip_strings_and_comments(src: str) -> str:
    """Reconstruct Python source with all STRING and COMMENT tokens
    blanked out (replaced with spaces) so a regex pattern check only
    sees executable code. Preserves line numbers and column offsets
    for accurate error messages."""
    lines = src.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenizeError:
        # Malformed source — return as-is; the linter will just see
        # whatever it would have seen before.
        return src
    for tok in tokens:
        if tok.type not in (tokenize.STRING, tokenize.COMMENT):
            continue
        start_row, start_col = tok.start
        end_row, end_col = tok.end
        # Replace the token's characters with spaces in-place across
        # the affected line(s).
        if start_row == end_row:
            line = lines[start_row - 1]
            lines[start_row - 1] = line[:start_col] + " " * (end_col - start_col) + line[end_col:]
        else:
            # Multi-line string: blank from start_col to EOL on first
            # line, full blank for middle lines, 0..end_col on last.
            first = lines[start_row - 1]
            lines[start_row - 1] = first[:start_col] + " " * (len(first) - start_col)
            for r in range(start_row, end_row - 1):
                lines[r] = " " * len(lines[r])
            last = lines[end_row - 1]
            lines[end_row - 1] = " " * end_col + last[end_col:]
    return "".join(lines)


def test_no_raw_cluster_sentinels_in_python():
    """Cluster IDs use CLUSTER_UNASSIGNED / CLUSTER_DISMISSED from
    bpp.constants — never the raw int.

    Strips strings + comments before matching so docstrings that
    explain the sentinels ("...unassigned (cluster_id = -1)...") don't
    false-positive.
    """
    bad = re.compile(
        r"cluster_id[^A-Za-z_\n]{0,30}-[12]\b|"
        r"-[12]\b[^A-Za-z_\n]{0,30}cluster_id",
    )
    # bpp/constants.py DEFINES the constants; the literal -1/-2 appears
    # there by necessity.
    allow = {BPP_DIR / "constants.py"}
    offenders: list[str] = []
    for path, src in _iter_py_files(BPP_DIR, exclude=allow):
        scrubbed = _strip_strings_and_comments(src)
        for n, line in enumerate(scrubbed.splitlines(), start=1):
            if bad.search(line):
                # Quote the ORIGINAL line in the error so the reader
                # sees the real code, not the blanked-out version.
                original = src.splitlines()[n - 1]
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{n}: {original.strip()}")
    assert not offenders, (
        "Raw cluster sentinel found in production code. Use CLUSTER_UNASSIGNED "
        "(=-1) or CLUSTER_DISMISSED (=-2) from bpp.constants. Offenders:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Rule 2: no bare `Image.open(...)` — must be wrapped in `with` or `retry_io`
# ---------------------------------------------------------------------------


def test_no_bare_image_open():
    """PIL Image.open must be inside a `with` block OR wrapped in
    `retry_io(Image.open, ...)`. A bare `Image.open(path)` pins the
    file descriptor until garbage collection.

    Uses the strings-and-comments-stripped source so docstring
    examples in the helper module ("...do NOT call Image.open
    directly") don't false-positive.
    """
    bad_re = re.compile(r"\bImage\.open\(")
    offenders: list[str] = []
    for path, src in _iter_py_files(BPP_DIR):
        scrubbed = _strip_strings_and_comments(src)
        for n, scrub_line in enumerate(scrubbed.splitlines(), start=1):
            if not bad_re.search(scrub_line):
                continue
            # Allowed wrappers — check the SCRUBBED line (string/comment
            # noise can't sneak past).
            if "with Image.open" in scrub_line:
                continue
            if "retry_io(Image.open" in scrub_line:
                continue
            original = src.splitlines()[n - 1]
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{n}: {original.strip()}")
    assert not offenders, (
        "Bare Image.open() without `with` or `retry_io`. Use one of:\n"
        "  with Image.open(path) as img: ...\n"
        "  img = retry_io(Image.open, path, label='...')\n"
        "Offenders:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Rule 3: no raw `sqlite3.connect()` outside the connection pool module
# ---------------------------------------------------------------------------


def test_no_raw_sqlite3_connect_outside_pool():
    """All DB connections go through get_db() / ctx.get_conn() from
    bpp.db.connection. The pool module itself is the only legitimate
    sqlite3.connect call site."""
    bad_re = re.compile(r"\bsqlite3\.connect\(")
    # backup.py / integrity.py split out of connection.py (LOC gate,
    # 2026-06-12) — they operate on possibly-corrupt DBs the pool must
    # never touch, so raw connects are correct there.
    allow = {
        BPP_DIR / "db" / "connection.py",
        BPP_DIR / "db" / "backup.py",
        BPP_DIR / "db" / "integrity.py",
    }
    offenders: list[str] = []
    for path, src in _iter_py_files(BPP_DIR, exclude=allow):
        for n, line in enumerate(src.splitlines(), start=1):
            if re.match(r"\s*#", line):
                continue
            if bad_re.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
    assert not offenders, (
        "Raw sqlite3.connect() outside bpp/db/connection.py. Use "
        "ctx.get_conn() or get_db(). Offenders:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Rule 4: every mutating endpoint in bpp/web/bp_*.py has @requires_local_app
# ---------------------------------------------------------------------------


_MUTATING_METHODS = frozenset({"post", "put", "delete", "patch"})

# Endpoints that are intentionally unauthenticated (pre-auth bootstrap,
# public health checks, etc.). Allowlist by route literal so a typo in
# a NEW unauthenticated endpoint surfaces as a test failure, not a
# silent pass.
_AUTH_ALLOWLIST_ROUTES = {
    # Pairing flow bootstrap — gated by fingerprint cookie + IP rate
    # limit in authorize_request, see bpp/web/share.py docstring.
    "/api/v1/share/pair/request",
}


def _walk_decorators(fn: ast.FunctionDef) -> list[str]:
    names: list[str] = []
    for dec in fn.decorator_list:
        # @requires_local_app → ast.Name("requires_local_app")
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        # @bp.post("/...") → ast.Call(func=ast.Attribute(attr="post"))
        elif isinstance(dec, ast.Call):
            f = dec.func
            if isinstance(f, ast.Attribute):
                names.append(f.attr)
            elif isinstance(f, ast.Name):
                names.append(f.id)
        elif isinstance(dec, ast.Attribute):
            names.append(dec.attr)
    return names


def _route_arg(fn: ast.FunctionDef) -> str | None:
    """Return the literal route string from @bp.<method>('/path/...')."""
    for dec in fn.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in _MUTATING_METHODS
            and dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            return dec.args[0].value
    return None


def test_mutating_endpoints_have_local_app_auth():
    """Every POST/PUT/DELETE/PATCH in bpp/web/bp_*.py must carry
    @requires_local_app (or be in the allowlist of intentionally-
    public endpoints)."""
    bp_files = sorted((BPP_DIR / "web").glob("bp_*.py"))
    offenders: list[str] = []
    for path in bp_files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            decs = _walk_decorators(node)
            # Must have a mutating-method decorator (otherwise it's not
            # an endpoint we care about).
            if not any(m in decs for m in _MUTATING_METHODS):
                continue
            route = _route_arg(node)
            if route in _AUTH_ALLOWLIST_ROUTES:
                continue
            if "requires_local_app" not in decs:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                    f"{node.name}() — route {route!r} missing @requires_local_app"
                )
    assert not offenders, (
        "Mutating endpoint without @requires_local_app. Either add the "
        "decorator (typical) or add the route to _AUTH_ALLOWLIST_ROUTES "
        "above with justification. Offenders:\n  " + "\n  ".join(offenders)
    )
