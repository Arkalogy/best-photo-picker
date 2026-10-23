"""Once pip runs for a feature, its packages (and the libraries the
install writes that the app imports) aren't loaded by this process again:
they load after a restart. Packages already loaded keep working."""

from __future__ import annotations

import sys

import pytest

from bpp.scoring import face_recognition_loader, install_hold
from bpp.scoring.model_base import ModelSingleton


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(install_hold, "_installed", frozenset())
    monkeypatch.setattr(install_hold, "_held", frozenset())


@pytest.fixture
def fresh_pkg(tmp_path, monkeypatch):
    """A package on the path that this process hasn't imported, belonging
    to a feature 'testfeat'."""
    (tmp_path / "fresh_feature_pkg.py").write_text("X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "fresh_feature_pkg", raising=False)
    monkeypatch.setitem(install_hold.FEATURE_MODULES, "testfeat", frozenset({"fresh_feature_pkg"}))
    yield "fresh_feature_pkg"
    sys.modules.pop("fresh_feature_pkg", None)


def _singleton(module: str) -> ModelSingleton:
    return ModelSingleton(
        name="test model",
        model_path=None,
        model_url=None,
        create_fn=lambda _path: "model",
        registry_id="openai_clip_vit_b32_onnx",
        import_check=install_hold.requires(module),
    )


def test_an_installed_feature_loads_only_after_a_restart(fresh_pkg):
    install_hold.hold_until_restart("testfeat")
    model = _singleton(fresh_pkg)
    assert install_hold.importable(fresh_pkg) is False
    assert model.is_available() is False
    assert model.get() is None
    with pytest.raises(install_hold.HeldForRestart, match="restart"):
        install_hold.import_gated(fresh_pkg)
    assert fresh_pkg not in sys.modules


def test_other_features_keep_loading(fresh_pkg):
    install_hold.hold_until_restart("inpaint")
    assert install_hold.importable(fresh_pkg) is True
    assert install_hold.importable("ast") is True


def test_a_package_already_loaded_keeps_working(fresh_pkg):
    __import__(fresh_pkg)
    install_hold.hold_until_restart("testfeat")
    assert install_hold.held_for_restart(fresh_pkg) is False
    assert install_hold.import_gated(fresh_pkg)


def test_its_unloaded_parts_are_held(monkeypatch):
    # scipy.ndimage loaded earlier; scipy.cluster not yet: new files.
    import scipy.ndimage  # noqa: F401

    monkeypatch.delitem(sys.modules, "scipy.cluster", raising=False)
    monkeypatch.delitem(sys.modules, "scipy.cluster.hierarchy", raising=False)
    install_hold.hold_until_restart("faces")
    with pytest.raises(install_hold.HeldForRestart):
        install_hold.import_gated("scipy.cluster.hierarchy")
    assert install_hold.import_gated("scipy.ndimage")


def test_the_libraries_an_install_writes_are_held_too():
    install_hold.hold_until_restart("faces")
    for module in ("face_recognition", "dlib", "mediapipe", "scipy"):
        assert module.partition(".")[0] in install_hold._held, module
    assert install_hold.held_keys() == {"faces"}


def test_dlib_says_restart_only_when_faces_were_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "face_recognition", None)  # not installed
    face_recognition_loader._reset_for_tests()
    try:
        install_hold.hold_until_restart("inpaint")  # unrelated install
        why = face_recognition_loader.load_face_recognition_or_reason()[1]
        assert why.startswith("not installed")
        monkeypatch.delitem(sys.modules, "face_recognition")
        install_hold.hold_until_restart("faces")
        why = face_recognition_loader.load_face_recognition_or_reason()[1]
        assert why == install_hold.RESTART_REASON
    finally:
        face_recognition_loader._reset_for_tests()


def test_looks_installed_does_not_import_face_recognition(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(install_hold.importlib, "import_module", lambda n: calls.append(n))
    face_recognition_loader._reset_for_tests()
    try:
        face_recognition_loader.looks_installed()
        assert calls == [], "the 30-second poll mustn't import dlib"
    finally:
        face_recognition_loader._reset_for_tests()


def test_every_model_package_check_goes_through_the_gate():
    import pathlib
    import re

    root = pathlib.Path(install_hold.__file__).parents[1]
    raw = [
        f"{p.relative_to(root)}: {m.group(0)}"
        for p in sorted(root.rglob("*.py"))
        if p.name != "model_base.py"  # its docstring example
        for m in re.finditer(r"import_check=(?!requires\()[^\n]*", p.read_text())
    ]
    assert raw == [], "use import_check=requires(...) so an installed feature waits"


def test_the_app_imports_scipy_only_through_the_gate():
    import pathlib
    import re

    root = pathlib.Path(install_hold.__file__).parents[1]
    raw = [
        f"{p.relative_to(root)}: {m.group(0)}"
        for p in sorted(root.rglob("*.py"))
        for m in re.finditer(r"^\s*(?:from scipy\b|import scipy\b)[^\n]*", p.read_text(), re.M)
    ]
    assert raw == [], "import scipy via install_hold.import_gated"


def test_a_hold_changes_a_file_never_the_environment(monkeypatch):
    # Changing the environment while threads run isn't safe: it's named
    # once at startup; the processes the app starts read the file.
    import os

    before = dict(os.environ)
    install_hold.hold_until_restart("faces")
    assert dict(os.environ) == before
    assert "dlib" in install_hold._own_file.read_text().split(",")
    install_hold.release("faces")
    assert not install_hold._own_file.exists()  # nothing held: no file


def test_a_process_the_app_started_reads_the_app_s_list(tmp_path, monkeypatch):
    held = tmp_path / "held-1.txt"
    held.write_text("scipy,dlib")
    monkeypatch.setenv(install_hold._ENV, str(held))
    assert install_hold._parent_file() == held
    assert install_hold._read(held) == {"scipy", "dlib"}
    # A stale name of its own (a crashed app with this process id): not a parent.
    monkeypatch.setenv(install_hold._ENV, str(install_hold._own_file_path()))
    assert install_hold._parent_file() is None


def test_hold_files_of_apps_no_longer_running_are_swept(tmp_path, monkeypatch):
    import subprocess

    dead = subprocess.Popen(["true"])
    dead.wait()
    (tmp_path / f"held-{dead.pid}.txt").write_text("scipy")
    (tmp_path / "held-1.txt").write_text("scipy")  # pid 1 is always alive
    install_hold._sweep_stale_files(tmp_path)
    assert not (tmp_path / f"held-{dead.pid}.txt").exists()
    assert (tmp_path / "held-1.txt").exists()


def test_installed_but_unaccepted_face_recognition_asks_for_the_license(monkeypatch):
    from bpp.scoring import face_embed_sface_runtime as sface
    from bpp.web.install_status import install_state

    monkeypatch.setattr(sface, "_sface_available", lambda: False)
    monkeypatch.setattr(face_recognition_loader, "looks_installed", lambda: True)
    monkeypatch.setattr(face_recognition_loader, "license_accepted", lambda: False)
    state = install_state()
    assert state["face_license_needed"] is True
    assert state["face_installable"] is False


def test_a_server_started_from_another_bpp_process_is_still_the_app(tmp_path, monkeypatch):
    # The environment names its launcher's file (it imported bpp): the
    # server still lists its own holds for its analysis processes.
    launcher = tmp_path / "held-999999.txt"
    launcher.write_text("scipy")
    monkeypatch.setenv(install_hold._ENV, str(launcher))
    install_hold.become_app()
    assert install_hold._inherited == frozenset()
    install_hold.hold_until_restart("inpaint")
    assert "torch" in install_hold._own_file.read_text().split(",")


def test_a_broken_install_counts_as_not_importable(tmp_path, monkeypatch):
    # Installed but its native library won't load (OSError, not ImportError).
    (tmp_path / "broken_native_pkg.py").write_text("raise OSError('dlopen failed')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "broken_native_pkg", raising=False)
    assert install_hold.importable("broken_native_pkg") is False
