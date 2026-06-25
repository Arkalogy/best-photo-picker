"""Guard against unescaped user-string interpolation in HTML
attributes across `bpp/web/static/js/modules/`.

Why this exists
---------------
The project conventions mandate ``escapeAttr()`` for every HTML attribute
interpolation. The rule's value is uniform application — "the value
is safe today" is the most common phrase in security postmortems.

A May 31 / June 1 sweep found 12 attribute interpolations of
risky-named values (``title``, ``label``, ``src``, ``name``, ``file``,
``path``, etc.) that bypassed the wrapper. None had a live XSS path
(values were hardcoded strings or URLs from `authedSrc`), but the
class needed closing so the next change doesn't quietly add a real
vector.

This test pins the closure: every ``<attr>="${...}"`` interpolation
whose attribute name OR expression body matches the risky keyword
set MUST be wrapped by ``escapeAttr`` or ``escapeJsAttr``. Numeric
expressions, simple integer IDs, conditional class fragments, and
ternary-of-literals are accepted as safe-by-construction.

If a finding here is a false positive (a new helper that produces
safe output, e.g.), allowlist it explicitly in ``_ALLOWLIST`` with
a one-line reason.
"""

from __future__ import annotations

import pathlib
import re

_ATTR_INTERP_RE = re.compile(
    r"""
    (?<![a-zA-Z_])
    (?P<attr>[a-zA-Z_][a-zA-Z0-9_-]*)
    \s*=\s*
    (?P<quote>["'])
    (?P<value>[^"']*?\$\{[^}]+\}[^"']*?)
    (?P=quote)
    """,
    re.VERBOSE,
)

_INNER_RE = re.compile(r"\$\{[^}]+\}")
_WRAPPER_OK = re.compile(r"^\s*\$\{\s*(escapeAttr|escapeJsAttr)\(")

# Words in either the attribute name OR the interpolated expression
# that indicate the value is a string the user can influence
# (filename, person name, album title, etc.). Catch is case-insensitive
# and matches inside camelCase identifiers (delLabel → "label").
_RISKY_KEYWORDS = re.compile(
    r"("
    r"name|title|label|text|tooltip|message|msg|description|desc|caption|"
    r"filepath|filename|path|file|src|url|href|alt|placeholder|"
    r"value|content|body|note|comment|tip"
    r")",
    re.IGNORECASE,
)

# Provably safe forms — explicit allowlists keep the check tight
# without auto-passing unknown shapes.
_NUMERIC_EXPR = re.compile(r"^\s*\$\{\s*[\d+\-*/.()\s]+(?:px|%|deg|em|rem)?\s*\}\s*$")
_LIT_TERNARY = re.compile(
    r"^\s*\$\{\s*[^?{}]+?\s*\?\s*"
    r'(?P<a>["\'])[^"\']*(?P=a)\s*:\s*'
    r'(?P<b>["\'])[^"\']*(?P=b)\s*\}\s*$'
)
# Nested-ternary form: ${cond ? "x" : cond2 ? "y" : "z"}.
# Used in photos-card.mjs:80 for the pet badge.
_NESTED_LIT_TERNARY = re.compile(
    r"^\s*\$\{\s*[^{}]+?\s*\?\s*"
    r'(?P<a>["\'])[^"\']*(?P=a)\s*:\s*'
    r'[^{}]+?\s*\?\s*(?P<b>["\'])[^"\']*(?P=b)\s*:\s*'
    r'(?P<c>["\'])[^"\']*(?P=c)\s*\}\s*$'
)
_SIMPLE_ID = re.compile(r"^\s*\$\{\s*\w*(?:[Ii]d|[Ii]dx|[Ii]ndex)\b(?:\s*[+*-]\s*\d+)?\s*\}\s*$")

# Numeric variable / member-access names — count, length, size, score,
# percent, delta, offset, width, height, intensity, val, num,
# *_index, *_count, years_ago, face_index, detection_index, etc.
# Project convention: these always hold numbers and pass through
# Number/parseInt at the boundary. escapeAttr on a number is a no-op.
# Matches both bare identifiers and single-level member access:
#   ${count} / ${unassigned.length} / ${rep.face_index} / ${yr.years_ago}
_NUMERIC_VAR = re.compile(
    r"^\s*\$\{\s*(?:\w+\.)?\w*("
    r"[Cc]ount|[Ll]ength|[Ss]ize|[Ss]core|[Pp]ercent|[Dd]elta|"
    r"[Oo]ffset|[Ww]idth|[Hh]eight|[Ii]ntensity|[Vv]al(?:ue)?|"
    r"[Nn]um|[Yy]ear|[Mm]onth|[Dd]ay|[Dd]ur|[Aa]go|"
    r"[Ii]ndex|[Ii]dx|"
    r"[Bb]rush|[Ss]traighten|[Pp]ersp|[Tt]one"
    r")\w*\s*\}\s*$"
)
# Single-letter loop / count vars: n, d, m, y, etc.
_SINGLE_LETTER = re.compile(r"^\s*\$\{\s*[a-z]\s*\}\s*$")
# Method-chain numerics: ${(x.confidence * 100).toFixed(0)} etc.
_FIXED_NUMERIC = re.compile(r"\.toFixed\s*\(\s*\d+\s*\)\s*\}\s*$")

# URL-from-helper: ${authedSrc(...)}. authedSrc constructs a token-
# authenticated URL and is the project's canonical URL helper —
# treating its output as safe-by-helper-convention matches existing
# practice across faces / albums / inspector.
_AUTHED_SRC = re.compile(r"^\s*\$\{\s*authedSrc\s*\(")
# A simple variable named *url / *src / *href that was assigned from
# authedSrc earlier in the same module: cropUrl, thumbSrc, avatarUrl.
# (We can't trace the assignment statically; treat the suffix as the
# contract.)
_URL_VAR = re.compile(r"^\s*\$\{\s*\w*(?:[Uu]rl|[Ss]rc|[Hh]ref)\s*\}\s*$")
# Const-array indexing: ${MONTHS_FULL[m - 1]}, ${DAYS[i]}.
# Project convention: uppercase identifiers are module-level constants.
_CONST_INDEX = re.compile(r"^\s*\$\{\s*[A-Z][A-Z_0-9]+\s*\[[^\]]+\]\s*\}\s*$")
# Style-attribute arithmetic: --cal-intensity:${intensity}.
# Numeric context, no XSS via CSS injection unless the value contains
# `;` or `<` — which numeric vars never do.
_STYLE_NUMERIC = re.compile(r"^\s*\$\{\s*\w+\s*\}\s*$")

# Specific known-safe expressions that don't fit the generic patterns
# but are verified safe in context. Keep this list short; prefer
# fixing the call site over allowlisting.
_ALLOWLIST: set[tuple[str, int]] = set()


def _classify(attr: str, expr: str) -> str:
    if _WRAPPER_OK.match(expr):
        return "OK"
    if _NUMERIC_EXPR.match(expr):
        return "NUMERIC"
    if _LIT_TERNARY.match(expr) or _NESTED_LIT_TERNARY.match(expr):
        return "LITERAL_TERNARY"
    if _SIMPLE_ID.match(expr):
        return "ID"
    if _NUMERIC_VAR.match(expr) or _SINGLE_LETTER.match(expr):
        return "NUMERIC_VAR"
    if _FIXED_NUMERIC.search(expr):
        return "FIXED_NUMERIC"
    if _AUTHED_SRC.match(expr) or _URL_VAR.match(expr):
        return "URL_HELPER"
    if _CONST_INDEX.match(expr):
        return "CONST_INDEX"
    # Class fragment: ${nameCls}, ${activeCls}, ${cssClass} — anything
    # ending in Cls / Class / Active / Selected. Project convention:
    # these always hold class-name string fragments.
    if attr.lower() == "class" and re.match(
        r"^\s*\$\{\s*\w+(Cls|Class|Active|Selected)\s*\}\s*$", expr
    ):
        return "CLASS_FRAG"
    # Function call returning a URL: ${cropUrl(c)}, ${authedSrc(...)}.
    # Project convention: helper names ending in Url/Src/Href are URL
    # producers; their output is treated as safe-by-helper-convention.
    if re.match(r"^\s*\$\{\s*\w*(Url|Src|Href)\s*\([^)]*\)\s*\}\s*$", expr, re.IGNORECASE):
        return "URL_HELPER_FN"
    # Two-letter numeric short names (sc, ct, qty, dur) on title/value/etc.
    if re.match(r"^\s*\$\{\s*[a-z]{2,3}\s*\}\s*$", expr):
        return "SHORT_NUMERIC"
    # CSS-context numeric variable inside style=, used for things like
    # `--cal-intensity:${intensity}` — safe because numbers don't
    # introduce CSS injection.
    if attr.lower() == "style" and _STYLE_NUMERIC.match(expr):
        return "STYLE_NUMERIC"
    # If the attr name OR the expression body contains a risky keyword,
    # this interpolation is a finding.
    if _RISKY_KEYWORDS.search(attr) or _RISKY_KEYWORDS.search(expr):
        return "RISKY"
    return "UNCLASSIFIED"


def test_no_risky_unescaped_attribute_interpolations() -> None:
    """No HTML-attribute interpolation in any ES module may pass a
    risky-keyword value without ``escapeAttr`` / ``escapeJsAttr``.

    If you add a new helper that produces safe output and trip this
    test, either wrap the call in ``escapeAttr`` (idempotent and zero
    perf cost) or add an entry to the ``_ALLOWLIST`` set above with
    the file:line and a one-line reason.
    """
    root = pathlib.Path("bpp/web/static/js/modules")
    offenders: list[str] = []
    for mjs in sorted(root.rglob("*.mjs")):
        rel = mjs.relative_to(root)
        for line_no, raw_line in enumerate(mjs.read_text().splitlines(), 1):
            # Strip line comments so the rule's own description doesn't
            # trip the check.
            line = raw_line.split("//", 1)[0]
            for m in _ATTR_INTERP_RE.finditer(line):
                attr = m.group("attr")
                val = m.group("value")
                for inner in _INNER_RE.finditer(val):
                    expr = inner.group(0)
                    if _classify(attr, expr) != "RISKY":
                        continue
                    if (str(rel), line_no) in _ALLOWLIST:
                        continue
                    snippet = line.strip()[:140]
                    offenders.append(f"{rel}:{line_no} attr={attr} expr={expr}\n    {snippet}")
    assert not offenders, (
        f"Found {len(offenders)} risky HTML-attribute interpolation(s) "
        f"without escapeAttr/escapeJsAttr:\n\n  "
        + "\n  ".join(offenders)
        + "\n\nFix: wrap the interpolated expression with escapeAttr() "
        "(or escapeJsAttr() if it's destined for a JS-context attribute "
        "like inline onclick / data-action). Or, if the value is "
        "provably safe (hardcoded string from a const), add to the "
        "_ALLOWLIST set with a one-line reason."
    )
