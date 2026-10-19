"""A setting is read with ``cfg_get``, never with a fallback written at
the call site.

``config.get("key", <fallback>)`` is a second copy of a default: fine while
it agrees, a silent behaviour change for partial-config callers (plugins,
the strategy hook, tests) once the two drift — as the dedup thresholds
(10/8 vs 20/10), the selection similarity threshold (0.0 vs 0.85) and the
face-cluster threshold constant (0.55 vs 0.80) had. Every such site now
reads through :func:`bpp.config.cfg_get`, which has one source
(``DEFAULTS``); this scan keeps new copies out. Plugin-namespaced keys
are by contract not in DEFAULTS, so ``bpp/plugins/`` is left out.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from bpp.config import DEFAULTS, cfg_get

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CONFIG_NAMES = {"config", "cfg"}


def _fallback_sites(source: str, where: str) -> list[str]:
    """``site`` for every ``<config|cfg>.get("key", x)`` call on a setting."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", None)
        if node.func.attr != "get" or owner_name not in _CONFIG_NAMES or len(node.args) != 2:
            continue
        key = node.args[0]
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        if key.value.startswith("_"):
            continue  # a runtime-injected key, not a setting
        found.append((node.lineno, f"{where}:{node.lineno} ({key.value})"))
    return [site for _, site in sorted(found)]


def test_the_scan_sees_every_shape() -> None:
    sample = (
        'a = config.get("max_per_day", 3)\n'
        'b = float(ctx.config.get(\n    "face_detection_confidence", 0.3\n))\n'
        'c = cfg.get("face_cluster_threshold", SOME_CONSTANT)\n'
        'd = other.get("not_a_config", 1)\n'
        'e = config.get("_face_extract_workers", 1)\n'  # runtime key: skipped
        'f = config.get("max_per_month")\n'  # no fallback: not a copy
    )
    assert [s.split(" ")[1] for s in _fallback_sites(sample, "sample.py")] == [
        "(max_per_day)",
        "(face_detection_confidence)",
        "(face_cluster_threshold)",
    ]


def test_no_setting_is_read_with_a_fallback_written_at_the_call_site() -> None:
    found = []
    for path in sorted((_ROOT / "bpp").rglob("*.py")):
        if "plugins" in path.relative_to(_ROOT).parts:
            continue
        found += _fallback_sites(path.read_text(encoding="utf-8"), str(path.relative_to(_ROOT)))
    assert found == [], "read these through cfg_get(config, key) instead:\n" + "\n".join(found)


class TestCfgGet:
    def test_reads_the_setting_when_present(self) -> None:
        assert cfg_get({"max_per_day": 7}, "max_per_day") == 7

    def test_falls_back_to_the_one_table(self) -> None:
        assert cfg_get({}, "max_per_day") == DEFAULTS["max_per_day"]
        assert cfg_get(None, "max_per_day") == DEFAULTS["max_per_day"]

    def test_an_unknown_key_is_a_typo_not_a_setting(self) -> None:
        with pytest.raises(KeyError):
            cfg_get({}, "max_per_dya")
