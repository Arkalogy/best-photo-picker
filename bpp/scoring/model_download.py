"""Fetching a ``ModelSingleton``'s weights: the retry loop behind
``ModelSingleton._download`` (split out of ``model_base.py`` for the
500-line cap). Transient network errors retry with backoff; an
integrity failure or a configuration refusal (unpinned / non-HTTPS
entry) propagates at once."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from bpp.scoring.model_base import ModelSingleton

log = get_logger(__name__)

_MAX_ATTEMPTS = 3


def download_model(model: ModelSingleton, repairing: bool) -> tuple[Path | None, str | None]:
    """Download ``model``'s weights to its ``model_path``. Returns
    ``(path, None)`` on success, ``(None, reason)`` when every attempt
    failed; raises ``ModelIntegrityError`` / ``ValueError`` for failures
    that retrying can't fix."""
    import time

    from bpp.scoring.model_base import ModelIntegrityError
    from bpp.utils.download import (
        DownloadSkippedError,
        announce,
        download_file,
        download_temp_path,
        plain_reason,
    )

    assert model.model_path is not None and model.model_url is not None
    try:
        model.model_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Can't create the folder for %s: %s", model.name, exc)
        return None, plain_reason(exc)
    log.info("Downloading %s to %s ...", model.name, model.model_path)
    announce(
        f"Re-downloading {model.name} — the saved copy was damaged…"
        if repairing
        else f"Downloading {model.name}…"
    )
    tmp_path = Path(download_temp_path(str(model.model_path)))
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            download_file(
                model.model_url,
                str(tmp_path),
                registry_id=model.registry_id,
                sha256=model.model_sha256,
            )
            os.replace(tmp_path, model.model_path)
            size_mb = model.model_path.stat().st_size // (1024 * 1024)
            log.info("%s downloaded (%d MB)", model.name, size_mb)
            return model.model_path, None
        except ModelIntegrityError:
            # SHA mismatch is unrecoverable — propagate immediately so
            # the caller hears about tampering/corruption without retry.
            tmp_path.unlink(missing_ok=True)
            log.error("Integrity failure downloading %s from %s", model.name, model.model_url)
            raise
        except ValueError:
            # download_file refuses an unpinned or non-HTTPS entry: a
            # configuration fault, never worth retrying.
            tmp_path.unlink(missing_ok=True)
            log.error("Refused to download %s from %s", model.name, model.model_url)
            raise
        except DownloadSkippedError as exc:
            # The registry says no (license not accepted, entry
            # withdrawn): retrying asks the same question again.
            tmp_path.unlink(missing_ok=True)
            log.info("Not downloading %s: %s", model.name, exc)
            return None, plain_reason(exc)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            if attempt < _MAX_ATTEMPTS:
                delay = 2**attempt
                log.warning(
                    "Failed to download %s (attempt %d/%d): %s — retrying in %ds",
                    model.name,
                    attempt,
                    _MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)
            else:
                log.error(
                    "Failed to download %s after %d attempts: %s",
                    model.name,
                    _MAX_ATTEMPTS,
                    exc,
                )
                return None, plain_reason(exc)
    return None, None
