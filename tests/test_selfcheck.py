"""``bpp selfcheck``: each check reports ok / missing / FAILED, and the
exit code fails on a broken library (always) or an absent one (only with
--require-all, the Mac app's release check)."""

from __future__ import annotations

import argparse
import sys

import pytest

from bpp.commands import selfcheck


def _raise(exc: BaseException):
    def check():
        raise exc

    return check


def _missing(name: str) -> ModuleNotFoundError:
    return ModuleNotFoundError(f"No module named '{name}'", name=name)


@pytest.mark.parametrize(
    "check, status",
    [
        (lambda: None, selfcheck.OK),
        (_raise(_missing("rawpy")), selfcheck.MISSING),
        # installed, but one of its own modules is gone (a partial install,
        # a module a frozen build left out): broken, not optional
        (_raise(_missing("rawpy._rawpy")), selfcheck.FAILED),
        # installed, but a package it needs is gone: broken, not optional
        (_raise(_missing("libraw_helper")), selfcheck.FAILED),
        (_raise(OSError("dlopen: code signature invalid")), selfcheck.FAILED),
        (_raise(ImportError("symbol not found")), selfcheck.FAILED),
    ],
)
def test_run_check_classifies_the_outcome(check, status):
    assert selfcheck.run_check("rawpy", check)[0] == status


def test_mediapipe_on_a_processor_without_avx_is_skipped_not_loaded(monkeypatch):
    """Loading mediapipe there would crash the whole selfcheck."""
    from bpp.scoring import cpu_support

    monkeypatch.setattr(cpu_support, "mediapipe_cpu_supported", lambda: False)
    # Any attempt to import it now fails (and would read as FAILED).
    monkeypatch.setitem(sys.modules, "mediapipe", None)
    status, detail = selfcheck.run_check("mediapipe", selfcheck._mediapipe)
    assert status == selfcheck.SKIPPED and "AVX" in detail


@pytest.mark.parametrize(
    "platform, lib", [("darwin", "libmediapipe.dylib"), ("linux", "libmediapipe.so")]
)
def test_mediapipe_check_loads_the_tasks_library_the_platform_uses(
    monkeypatch, tmp_path, platform, lib
):
    import types

    from bpp.scoring import cpu_support

    fake = types.ModuleType("mediapipe")
    fake.__file__ = str(tmp_path / "mediapipe" / "__init__.py")
    monkeypatch.setitem(sys.modules, "mediapipe", fake)
    monkeypatch.setattr(cpu_support, "mediapipe_cpu_supported", lambda: True)
    monkeypatch.setattr(selfcheck.sys, "platform", platform)
    # a release whose Tasks API runs on the C library
    monkeypatch.setattr(selfcheck.importlib.util, "find_spec", lambda name: object())
    loaded = []

    def refuse(path):
        loaded.append(path)
        raise OSError("library refused")

    monkeypatch.setattr(selfcheck.ctypes, "CDLL", refuse)
    status, detail = selfcheck.run_check("mediapipe", selfcheck._mediapipe)
    assert loaded == [str(tmp_path / "mediapipe" / "tasks" / "c" / lib)]
    assert status == selfcheck.FAILED and "library refused" in detail


@pytest.mark.parametrize("binding_loads, status", [(True, "ok"), (False, "FAILED")])
def test_an_older_mediapipe_is_checked_through_its_python_extension(
    monkeypatch, tmp_path, binding_loads, status
):
    """Releases before the C library (mediapipe>=0.10 allows them) have no
    tasks/c library to load; their native code is a Python extension."""
    import types

    from bpp.scoring import cpu_support

    fake = types.ModuleType("mediapipe")
    fake.__file__ = str(tmp_path / "mediapipe" / "__init__.py")
    monkeypatch.setitem(sys.modules, "mediapipe", fake)
    monkeypatch.setattr(cpu_support, "mediapipe_cpu_supported", lambda: True)
    monkeypatch.setattr(selfcheck.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(selfcheck.ctypes, "CDLL", lambda path: pytest.fail("no C library here"))
    imported = []

    def import_module(name):
        imported.append(name)
        if not binding_loads:
            raise ImportError("dlopen: library not loaded")

    monkeypatch.setattr(selfcheck.importlib, "import_module", import_module)
    assert selfcheck.run_check("mediapipe", selfcheck._mediapipe)[0] == status
    assert imported == ["mediapipe.python._framework_bindings"]


def test_a_failure_names_the_error():
    status, detail = selfcheck.run_check("x", _raise(OSError("library not loaded")))
    assert status == selfcheck.FAILED
    assert detail == "OSError: library not loaded"


def _run(monkeypatch, capsys, checks, require_all=False):
    monkeypatch.setattr(selfcheck, "CHECKS", checks)
    code = selfcheck.do_selfcheck(argparse.Namespace(require_all=require_all))
    return code, capsys.readouterr().out.splitlines()


def test_exit_code_fails_on_a_broken_library_and_only_strictly_on_a_missing_one(
    monkeypatch, capsys
):
    ok = ("A", "a", lambda: None)
    missing = ("B", "b", _raise(_missing("b")))
    broken = ("C", "c", _raise(OSError("refused")))

    code, lines = _run(monkeypatch, capsys, [ok, missing])
    assert code == 0
    assert lines == ["ok      A", "missing B"]

    assert _run(monkeypatch, capsys, [ok, missing], require_all=True)[0] == 1
    from bpp.scoring.cpu_support import CpuUnsupported

    skipped = ("D", "d", _raise(CpuUnsupported("d: no AVX", name="d")))
    assert _run(monkeypatch, capsys, [ok, skipped])[0] == 0
    assert _run(monkeypatch, capsys, [ok, skipped], require_all=True)[0] == 1
    code, lines = _run(monkeypatch, capsys, [ok, broken])
    assert code == 1
    assert lines[1] == "FAILED  C: OSError: refused"


def test_every_native_part_of_the_app_is_checked():
    """What the desktop app bundles (bppicker[web,heic,faces,raw,inpaint]
    plus the base install) has a check, so --require-all covers it."""
    tops = {top for _name, top, _check in selfcheck.CHECKS}
    assert {
        "numpy",
        "PIL",
        "cv2",
        "onnxruntime",
        "cryptography",
        "flask",
        "pillow_heif",
        "rawpy",
        "scipy",
        "mediapipe",
        "ai_edge_litert",
        "simple_lama_inpainting",
    } <= tops


def test_cli_routes_selfcheck(monkeypatch):
    from bpp import cli

    seen = {}
    monkeypatch.setattr(
        "bpp.commands.selfcheck.do_selfcheck", lambda args: seen.setdefault("args", args) and 0
    )
    assert cli.main(["selfcheck", "--require-all"]) == 0
    assert seen["args"].require_all is True
