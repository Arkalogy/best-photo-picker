"""Tests for AnalyzeWorker — archive extraction safety and JSON write atomicity."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path
from unittest import mock


class TestWriteAnalysisJson:
    """Verify that analysis.json is written atomically."""

    def test_success_writes_correct_content(self, tmp_path):
        from bpp.web.analyze_worker import _write_analysis_json

        path = str(tmp_path / "analysis.json")
        data = [{"id": 1, "score": 0.9}]
        _write_analysis_json(path, data)
        assert json.loads(Path(path).read_text()) == data

    def test_failure_preserves_original_file(self, tmp_path):
        from bpp.web.analyze_worker import _write_analysis_json

        path = str(tmp_path / "analysis.json")
        Path(path).write_text("ORIGINAL")

        with (
            mock.patch("json.dump", side_effect=OSError("disk full")),
            contextlib.suppress(OSError),
        ):
            _write_analysis_json(path, [{"id": 1}])

        assert Path(path).read_text() == "ORIGINAL", "original file must survive a failed write"

    def test_failure_leaves_no_tmp_file(self, tmp_path):
        from bpp.web.analyze_worker import _write_analysis_json

        path = str(tmp_path / "analysis.json")

        with (
            mock.patch("json.dump", side_effect=OSError("disk full")),
            contextlib.suppress(OSError),
        ):
            _write_analysis_json(path, [])

        assert list(tmp_path.glob("*.tmp")) == [], "no .tmp file should remain after failure"


class TestZipPathTraversal:
    """Verify that malicious zip entries are rejected."""

    def test_safe_zip_extracts_normally(self, tmp_path):
        """A normal zip file should extract without issues."""
        from bpp.web.analyze_worker import AnalyzeWorker

        # Create a safe zip
        zip_path = tmp_path / "safe.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("photo.jpg", b"fake image data")
            zf.writestr("subdir/another.jpg", b"more data")

        worker = AnalyzeWorker()
        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)

        worker.start(
            input_dir=str(zip_path),
            workdir=workdir,
            config={"max_long_side": 1024},
            extensions=[".jpg", ".png"],
        )
        worker._thread.join(timeout=10)

        extract_dir = os.path.join(workdir, "extracted")
        assert os.path.isdir(extract_dir)
        assert os.path.exists(os.path.join(extract_dir, "photo.jpg"))
        assert os.path.exists(os.path.join(extract_dir, "subdir", "another.jpg"))

    def test_path_traversal_zip_is_rejected(self, tmp_path):
        """A zip with ../ entries must be rejected."""
        from bpp.web.analyze_worker import AnalyzeWorker

        # Create a malicious zip with path traversal
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../etc/passwd", b"root:x:0:0")

        worker = AnalyzeWorker()
        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)

        worker.start(
            input_dir=str(zip_path),
            workdir=workdir,
            config={"max_long_side": 1024},
            extensions=[".jpg", ".png"],
        )
        worker._thread.join(timeout=10)

        # The worker should have reported an error
        messages = []
        while not worker.progress_queue.empty():
            messages.append(worker.progress_queue.get_nowait())

        # Should contain an error (not a successful extraction)
        error_msgs = [m for m in messages if m.get("type") == "error"]
        assert len(error_msgs) > 0, "Malicious zip should trigger an error"

        # The traversal target should NOT exist
        evil_path = os.path.join(workdir, "extracted", "../../etc/passwd")
        assert not os.path.exists(os.path.normpath(evil_path))


def _drain(worker):
    msgs = []
    while not worker.progress_queue.empty():
        msgs.append(worker.progress_queue.get_nowait())
    return msgs


class TestTarExtraction:
    """H4 regression: tar path needs the same size cap and traversal
    rejection that zips already get, plus error handling for the
    Py3.12+ tarfile filter raising on malicious entries."""

    def test_safe_tar_extracts_normally(self, tmp_path):
        from bpp.web.analyze_worker import AnalyzeWorker

        tar_path = tmp_path / "safe.tar"
        with tarfile.open(tar_path, "w") as tf:
            data = b"fake image data"
            info = tarfile.TarInfo("photo.jpg")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        worker = AnalyzeWorker()
        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        worker.start(
            input_dir=str(tar_path),
            workdir=workdir,
            config={"max_long_side": 1024},
            extensions=[".jpg", ".png"],
        )
        worker._thread.join(timeout=10)

        assert os.path.exists(os.path.join(workdir, "extracted", "photo.jpg"))

    def test_oversize_tar_rejected_with_message(self, tmp_path):
        """Tar with declared sizes over the cap must error out before
        extraction, matching the zip path's behavior."""
        from bpp.web.analyze_worker import AnalyzeWorker

        tar_path = tmp_path / "huge.tar"
        with tarfile.open(tar_path, "w") as tf:
            # Declare a member sized far beyond the cap. We don't actually
            # write that many bytes — the cap check looks at metadata.
            info = tarfile.TarInfo("monster.bin")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))

        worker = AnalyzeWorker()
        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)

        # Patch the cap to a tiny value so the test doesn't have to forge
        # a 50GB tar — the contract we're testing is "size cap exists".
        with mock.patch("bpp.web.analyze_archive._MAX_ARCHIVE_BYTES", 0):
            worker.start(
                input_dir=str(tar_path),
                workdir=workdir,
                config={"max_long_side": 1024},
                extensions=[".jpg", ".png"],
            )
            worker._thread.join(timeout=10)

        errors = [m for m in _drain(worker) if m.get("type") == "error"]
        assert errors, "oversize tar should emit an error"
        assert "too large" in errors[0]["message"].lower()

    def test_path_traversal_tar_is_rejected(self, tmp_path):
        """The Py3.12+ filter='data' raises FilterError on these.
        Pre-3.12 our manual loop raises ValueError. Either way the
        worker must report a clean error, not crash."""
        from bpp.web.analyze_worker import AnalyzeWorker

        tar_path = tmp_path / "evil.tar"
        with tarfile.open(tar_path, "w") as tf:
            data = b"root:x:0:0"
            info = tarfile.TarInfo("../../etc/passwd")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        worker = AnalyzeWorker()
        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        worker.start(
            input_dir=str(tar_path),
            workdir=workdir,
            config={"max_long_side": 1024},
            extensions=[".jpg", ".png"],
        )
        worker._thread.join(timeout=10)

        errors = [m for m in _drain(worker) if m.get("type") == "error"]
        assert errors, "path-traversal tar should emit an error"
        evil = os.path.normpath(os.path.join(workdir, "extracted", "../../etc/passwd"))
        assert not os.path.exists(evil)
