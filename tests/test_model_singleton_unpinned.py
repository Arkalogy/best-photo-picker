"""A model whose download isn't pinned to a sha256 is refused the way
the docstring promises: ``ensure_model()`` returns None with a logged
reason, ``ensure_model_or_raise()`` says why; neither leaks a
ValueError into the analysis preflight."""

from __future__ import annotations

import pytest


def _unpinned(tmp_path):
    from bpp.scoring.model_base import ModelSingleton

    return ModelSingleton(
        name="unpinned",
        model_path=tmp_path / "unpinned.onnx",
        model_url="https://example.invalid/unpinned.onnx",
        create_fn=lambda p: object(),
        registry_id="opencv_yunet",
        model_sha256=None,
    )


def test_ensure_model_returns_none_for_an_unpinned_download(tmp_path, caplog):
    model = _unpinned(tmp_path)
    assert model.ensure_model() is None
    assert "sha256" in caplog.text


def test_ensure_model_or_raise_says_why(tmp_path):
    model = _unpinned(tmp_path)
    with pytest.raises(RuntimeError, match="sha256"):
        model.ensure_model_or_raise()
