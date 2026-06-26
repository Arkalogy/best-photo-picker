"""Registry-suite-wide test isolation.

The registry has process-global state — entries dict, T7
reclassification lock, BYOM store path, etc. Individual test
files reach into that state freely (a test_model_registry test
needs an empty registry to assert nothing's registered; a
test_signed_manifest test seeds a restricted baseline by hand;
test_policy resets the T7 lock between cases).

The downstream price was order-dependent breakage: any test in
this directory that wiped the registry left ``sface_yunet``
gone for the next file, which then asserted "removing the
built-in entry refuses" against an empty registry and got a
"no such entry" error instead.

This conftest re-seeds the bundled built-ins (SFace + dlib)
AFTER every test in the directory, so each test starts from
the same baseline the app starts from at boot. Tests that need
an empty registry still reset it themselves on setup; the
teardown here just guarantees the next test inherits a clean
post-import state.

Files reset by the registry tests today:

* ``bpp.registry.model_registry._registry`` — the entries dict
* ``bpp.registry.policy._reclassification_lock`` — T7 state
"""

from __future__ import annotations

import pytest

from bpp.registry.builtins import register_builtins
from bpp.registry.model_registry import _reset_registry_for_tests
from bpp.registry.policy import _reset_reclassification_lock_for_tests


@pytest.fixture(autouse=True)
def _registry_isolation():
    """Restore the bundled-baseline registry + clean T7 lock after
    each test in this directory. Runs at conftest scope so it
    fires AFTER any module-level autouse fixture cleanup, giving
    later test files a deterministic post-import baseline."""
    yield
    _reset_registry_for_tests()
    _reset_reclassification_lock_for_tests()
    register_builtins()
