"""In the container image, per-user state is not inside the library folder.

The image used to keep the config and cache folders under ``/data``, the
folder it opens as the library. A library folder prepared by someone else
could then carry model-licence acceptances, the use declaration and the
sharing key with it. They now live under ``/state``, a separate mount.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text().replace("\\\n", " ")


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in DOCKERFILE.splitlines():
        if line.startswith("ENV "):
            env.update(re.findall(r"(\w+)=(\S+)", line))
    return env


def _library() -> str:
    cmd = next(line for line in DOCKERFILE.splitlines() if line.startswith("CMD "))
    return re.search(r'"--library",\s*"([^"]+)"', cmd).group(1)


def test_the_image_keeps_config_and_cache_outside_the_library_folder():
    env, library = _env(), _library()

    for key in ("XDG_CONFIG_HOME", "BPP_CACHE_DIR"):
        assert not (env[key] + "/").startswith(library + "/"), f"{key}={env[key]}"


def test_per_user_state_resolves_outside_the_library_under_the_image_settings(monkeypatch):
    from bpp.registry import manifest_rollback
    from bpp.registry.acceptance_log import get_acceptance_log_path
    from bpp.registry.byom import get_byom_store_path
    from bpp.registry.use_context_store import get_use_context_path
    from bpp.utils.paths import models_dir
    from bpp.web.share_seal_store import key_file, state_file

    env, library = _env(), _library()
    monkeypatch.delenv("BPP_ACCEPTANCE_LOG_PATH", raising=False)
    for key in ("XDG_CONFIG_HOME", "BPP_CACHE_DIR", "HOME"):
        monkeypatch.setenv(key, env[key])

    paths = [
        get_acceptance_log_path(),
        get_use_context_path(),
        get_byom_store_path(),
        manifest_rollback.state_path(),
        models_dir(),
        key_file(),
        state_file(),
    ]
    for path in paths:
        assert not str(path).startswith(library + "/"), f"{path} is inside the library"


def test_every_documented_container_command_mounts_the_state_folder():
    readme = (REPO_ROOT / "README.md").read_text().replace("\\\n", " ")
    commands = [line for line in readme.splitlines() if line.lstrip().startswith("docker run")]
    assert len(commands) >= 3
    for command in commands:
        assert ":/state" in command, command


def test_serve_warns_when_per_user_state_sits_inside_the_library(tmp_path, monkeypatch, caplog):
    import argparse
    import logging

    from bpp.commands.serve import _resolve_library

    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(lib / ".config"))
    with caplog.at_level(logging.WARNING):
        _resolve_library(argparse.Namespace(library=str(lib)))

    assert any("inside the library folder" in r.getMessage() for r in caplog.records)


def test_a_key_that_cannot_be_saved_does_not_stop_the_app(tmp_path, monkeypatch):
    from bpp.web import share_seal_store as store

    def no_write(path, text, *, once):
        raise PermissionError("read-only config folder")

    monkeypatch.setattr(store, "_write_private", no_write)
    monkeypatch.setattr(store, "_memory_keys", {})
    monkeypatch.setattr(store, "_memory_states", {})

    first = store.key()
    assert len(first) == 64
    assert store.key() == first, "one key per process"
    store.update(lambda state: state["libs"].update(x={"n": 1, "path": "/p"}))
    assert store.record("x") == {"n": 1, "path": "/p"}
