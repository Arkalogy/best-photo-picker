"""Every config fallback written into the app agrees with ``DEFAULTS``.

``config.get("key", <fallback>)`` is a second copy of a default: fine while
it agrees, a silent behaviour change for partial-config callers (plugins,
the strategy hook, tests) once the two drift — as the dedup thresholds
(10/8 vs 20/10), the selection similarity threshold (0.0 vs 0.85) and the
face-cluster threshold constant (0.55 vs 0.80) had. This locks the
remaining copies to the one table: literal numbers and UPPER_CASE
constants alike, however the call is laid out. Plugin-namespaced keys are
by contract not in DEFAULTS, so ``bpp/plugins/`` is left out.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from bpp import constants
from bpp.config import DEFAULTS

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CONFIG_NAMES = {"config", "cfg"}


_SKIP = object()  # a name that already reads DEFAULTS[<the same key>]
UNRESOLVED = "unresolved"  # a fallback the scan can't judge: must be made plain


def _module_constants(tree: ast.Module) -> dict[str, object]:
    """UPPER_CASE names assigned at module level: a number, or
    ``DEFAULTS["key"]`` (recorded as ``("defaults", key)``)."""
    names: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id.isupper():
            names[target.id] = _constant_value(value)
    return names


def _constant_value(node: ast.expr) -> object:
    """A number or text, ``("defaults", key)`` for ``DEFAULTS["key"]``, else UNRESOLVED."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float | str):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int | float)
    ):
        return -node.operand.value
    if (
        isinstance(node, ast.Subscript)
        and getattr(node.value, "id", None) == "DEFAULTS"
        and isinstance(node.slice, ast.Constant)
    ):
        return ("defaults", node.slice.value)
    return UNRESOLVED


def _fallbacks_in(source: str, where: str) -> list[tuple[str, str, object]]:
    """``(site, key, fallback)`` for every ``<config|cfg>.get("key", x)`` call."""
    found = []
    tree = ast.parse(source)
    local = _module_constants(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", None)
        if node.func.attr != "get" or owner_name not in _CONFIG_NAMES or len(node.args) != 2:
            continue
        key, fallback = node.args
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        if key.value.startswith("_"):
            continue  # a runtime-injected key, not a setting
        site = f"{where}:{node.lineno}"
        if isinstance(fallback, ast.Name) and fallback.id.isupper():
            value = local.get(fallback.id, getattr(constants, fallback.id, UNRESOLVED))
        else:
            value = _constant_value(fallback)
        if value == ("defaults", key.value):
            continue  # single-source already
        found.append((site, key.value, value))
    return found


def _app_fallbacks() -> list[tuple[str, str, object]]:
    found = []
    for path in sorted((_ROOT / "bpp").rglob("*.py")):
        if "plugins" in path.relative_to(_ROOT).parts:
            continue
        found += _fallbacks_in(path.read_text(encoding="utf-8"), str(path.relative_to(_ROOT)))
    return found


_FOUND = _app_fallbacks()


def test_the_scan_sees_every_shape() -> None:
    sample = (
        'a = config.get("max_per_day", 3)\n'
        'b = float(ctx.config.get(\n    "face_detection_confidence", 0.3\n))\n'
        'c = cfg.get("face_cluster_threshold", FACE_CLUSTER_THRESHOLD_FALLBACK)\n'
        'd = other.get("not_a_config", 1)\n'
        "_LOCAL = 7\n"
        "_TYPED: int = 9\n"
        '_FROM_TABLE = DEFAULTS["max_per_month"]\n'
        'e = config.get("min_per_month", _LOCAL)\n'
        'f = config.get("max_per_month", _FROM_TABLE)\n'  # single-source already: skipped
        'g = config.get("follow_symlinks", False)\n'
        'h = config.get("max_per_month", -1)\n'
        'i = config.get("time_window_seconds", 2 * 30)\n'  # can't judge: flagged
        'j = config.get("min_per_month", _TYPED)\n'
        'k = config.get("hash_distance_threshold", DEFAULTS["time_window_seconds"])\n'  # wrong key
        'l = config.get("_face_extract_workers", 1)\n'  # runtime key: skipped
        'm = config.get("sensitive_in_picks", "allow")\n'
    )
    seen = {(key, fallback) for _, key, fallback in _fallbacks_in(sample, "sample.py")}
    assert seen == {
        ("max_per_day", 3),
        ("face_detection_confidence", 0.3),
        ("face_cluster_threshold", constants.FACE_CLUSTER_THRESHOLD_FALLBACK),
        ("min_per_month", 7),
        ("follow_symlinks", False),
        ("max_per_month", -1),
        ("time_window_seconds", UNRESOLVED),
        ("min_per_month", 9),
        ("hash_distance_threshold", ("defaults", "time_window_seconds")),
        ("sensitive_in_picks", "allow"),
    }


def test_the_app_has_fallbacks_to_lock() -> None:
    assert _FOUND  # the scan is alive against the real tree


@pytest.mark.parametrize(("site", "key", "fallback"), _FOUND, ids=[f[0] for f in _FOUND])
def test_fallback_matches_defaults(site: str, key: str, fallback: object) -> None:
    assert key in DEFAULTS, f"{site}: DEFAULTS has no {key!r} — is the key spelled right?"
    assert fallback != UNRESOLVED, (
        f"{site}: the fallback for {key!r} can't be judged — write DEFAULTS[{key!r}] instead"
    )
    assert not isinstance(fallback, tuple), (
        f"{site}: {key!r} falls back to DEFAULTS[{fallback[1]!r}] — a different key"
    )
    default = DEFAULTS[key]
    assert isinstance(default, int | float | str), (
        f"{site}: {key!r} defaults to {default!r} — a plain fallback for it is wrong"
    )
    if isinstance(default, bool | str) or isinstance(fallback, bool | str):
        assert default == fallback, (
            f"{site}: {key!r} falls back to {fallback!r}, DEFAULTS says {default!r}"
        )
    else:
        assert float(default) == float(fallback), (
            f"{site}: {key!r} falls back to {fallback!r}, DEFAULTS says {default!r}"
        )
