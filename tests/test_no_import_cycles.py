"""Import-cycle gate — fails fast if any pytest discovery would dead-lock
on a circular module load.

P0 of refactor-plan.md. During the v0.1 split I introduced several
lazy-import workarounds to break cycles (smart_album_people pulls
_ensure_smart_album back from smart_albums at call time;
photos_lifecycle pulls PHOTO_COLS_SLIM from photos at call time;
schema_migrate pulls SCHEMA_VERSION from schema at call time). The
lazy-import band-aid works but each one is a code smell — a P3/P4
refactor that re-introduces a top-level cycle won't show up as a
test failure until something actually imports the new combination.

This gate runs ``pytest --collect-only`` semantics — every test module
is imported as part of collection, which transitively imports the
project tree. If a top-level cycle exists, collection deadlocks or
raises ImportError, and pytest itself fails to run.

For an extra explicit signal we also walk every top-level Python
module under ``bpp/`` and import it. A subtle cycle that only fires
on a specific import order will raise ``ImportError`` here even when
the test-collection path got lucky.
"""

from __future__ import annotations

import importlib
import pkgutil

import bpp


def _all_module_names() -> list[str]:
    """Walk every submodule of the bpp package."""
    names: list[str] = []
    for module_info in pkgutil.walk_packages(bpp.__path__, prefix="bpp."):
        # Skip plugin example dirs and migration step modules — these are
        # imported lazily by registries and aren't part of the main graph.
        if ".plugins.example" in module_info.name:
            continue
        names.append(module_info.name)
    return sorted(names)


class TestImportCycleGate:
    def test_every_bpp_module_imports_cleanly(self):
        """The big hammer: import every module top-down. Cycles that depend
        on import order, lazy imports that try to short-circuit a cycle but
        fail on cold cache, and accidental import-time side effects all
        surface here.
        """
        failures: list[tuple[str, str]] = []
        for name in _all_module_names():
            try:
                importlib.import_module(name)
            except Exception as exc:
                failures.append((name, f"{type(exc).__name__}: {exc}"))
        assert not failures, (
            "Top-level import-cycle gate failed for these modules:\n"
            + "\n".join(f"  - {n}: {msg}" for n, msg in failures)
            + "\n\nA cycle here will produce a queue-timeout or 'import "
            "deadlock' at production startup. Move the offending import "
            "to a lazy delegate or break the cycle by extracting the "
            "shared symbol into a third module."
        )

    def test_bpp_web_state_does_not_re_export_implementation(self):
        """state.py is the facade; state_init.py / state_lifecycle.py /
        state_helpers.py are the implementation. Top-level (column 0)
        imports of ``bpp.web.state`` from those impl modules would cycle.
        Indented imports inside function bodies are fine — that's the
        intentional lazy-delegate pattern.
        """
        for impl in ("state_init", "state_lifecycle"):
            mod_src = (importlib.resources.files("bpp.web") / f"{impl}.py").read_text()
            for line in mod_src.splitlines():
                # Only top-level (column 0, not indented) imports matter.
                if line.startswith("from bpp.web.state import") and not line.startswith(
                    (" ", "\t")
                ):
                    raise AssertionError(
                        f"{impl}.py: top-level import 'from bpp.web.state import' "
                        "creates a circular import — state.py imports this module. "
                        "Move to TYPE_CHECKING block or a lazy inline import."
                    )

    def test_smart_album_people_lazy_delegates_to_smart_albums(self):
        """Documented circular workaround: smart_album_people imports
        from smart_albums lazily inside _ensure_smart_album. A top-level
        import would re-enter the registry build at smart_albums import
        time and dead-lock. Indented imports inside function bodies
        (the lazy delegate) are the intentional pattern.
        """
        mod_src = (importlib.resources.files("bpp.db") / "smart_album_people.py").read_text()
        for line in mod_src.splitlines():
            # Only top-level (column 0) imports are banned.
            if line.startswith("from bpp.db.smart_albums import") and not line.startswith(
                (" ", "\t")
            ):
                raise AssertionError(
                    "smart_album_people.py: top-level import 'from bpp.db.smart_albums "
                    "import' breaks the lazy-delegate cycle workaround. Keep the "
                    "import inside _ensure_smart_album."
                )
