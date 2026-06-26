"""Export file-writer strategies, the pluggable ExportMode registry, and
image-processing helpers (resize / format / metadata-strip).

Split out of bpp/output/export.py for the 500-LOC cap. ``export_selected``
(the orchestrator) stays in export.py and imports these; the public names
``ExportMode`` / ``ExportModeRegistry`` / ``register_export_mode`` are
re-exported from export.py for back-compat with plugins.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from bpp.utils.logging import get_logger
from bpp.utils.retry import retry_io

log = get_logger(__name__)

_FMT_EXT = {"jpeg": ".jpg", "png": ".png"}


def _process_image(src: str, dest: str, fmt: str, max_size: int | None, quality: int) -> str:
    """Resize/convert an image, return the final dest path."""
    from PIL import Image

    # Context manager releases the source file handle when the block
    # exits. `img.convert(...)` returns a new Image instance independent
    # of the source file, so rebinding `img` inside the block is fine —
    # the original FD still closes correctly on exit.
    with Image.open(src) as img:
        # Resize if needed
        if max_size and max(img.size) > max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)

        # Determine output format and extension
        if fmt == "jpeg":
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            dest = _swap_ext(dest, ".jpg")
            img.save(dest, "JPEG", quality=quality)
        elif fmt == "png":
            dest = _swap_ext(dest, ".png")
            img.save(dest, "PNG")
        else:
            # original format but resized
            img.save(dest)

    return dest


def _swap_ext(path: str, new_ext: str) -> str:
    """Replace file extension."""
    base, _ = os.path.splitext(path)
    return base + new_ext


def _needs_processing(mode: str, fmt: str, max_size: int | None) -> bool:
    """Check if we need Pillow processing (format conversion or resize)."""
    return mode == "copy" and (fmt != "original" or max_size is not None)


_METADATA_STRIP_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def _copy_image_without_metadata(src: str, dest: str, quality: int) -> None:
    """Copy an image while dropping embedded metadata by re-encoding it."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(src) as img:
            fmt = img.format
            img = ImageOps.exif_transpose(img)
            save_kwargs: dict[str, Any] = {}
            if fmt == "JPEG":
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                save_kwargs["quality"] = quality
            img.save(dest, format=fmt, **save_kwargs)
    except (OSError, UnidentifiedImageError):
        log.warning("Could not strip image metadata from %s; copying original bytes", src)
        _export_copy(src, dest)


def _export_copy(src: str, dest: str) -> None:
    retry_io(shutil.copy2, src, dest, label="export-copy")


def _export_hardlink(src: str, dest: str) -> None:
    retry_io(os.link, src, dest, label="export-link")


def _export_symlink(src: str, dest: str) -> None:
    retry_io(os.symlink, os.path.abspath(src), dest, label="export-symlink")


# ── ZIP bundle ──────────────────────────────────────────────────────
#
# "zip" is a BUNDLE mode, not a per-photo handler: it can't satisfy the
# `(src, dest) -> None` contract because the whole point is to collect
# every photo into ONE archive. So export_selected() special-cases it —
# each photo is written into the staging `selected/` dir exactly as copy
# mode would (honoring format/resize/strip), then this helper packs that
# dir into a single .zip and the loose dir is removed. The registry entry
# below exists so "zip" is a recognized, listed mode (its handler is the
# copy handler, never actually dispatched — the orchestrator intercepts
# the mode before per-photo dispatch).

ZIP_BUNDLE_NAME = "best-photos.zip"


def bundle_selected_zip(selected_dir: str, archive_path: str, dest_names: list[str]) -> None:
    """Pack the named files under ``selected_dir`` into ``archive_path``.

    Files are stored flat (arcname = the per-photo dest filename, e.g.
    ``001_photo.jpg``) so unzipping yields a clean set of photos with no
    nested folders. Deflated to keep the handoff file small. Raises
    ``OSError`` on write failure (the caller leaves the loose folder in
    place so the export isn't lost).
    """

    def _write() -> None:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in dest_names:
                src = os.path.join(selected_dir, name)
                if os.path.isfile(src):
                    zf.write(src, arcname=name)

    retry_io(_write, label="export-zip-bundle")


@dataclass(frozen=True)
class ExportMode:
    """Metadata for a single export-mode handler.

    `name` is the user-facing key used by the API (`mode="copy"` etc).
    `handler(src, dest)` is the per-photo callable; it receives the
    source filepath on disk and a fully-resolved destination path
    (the loop has already created the parent directory and applied
    safe-join validation). Return value is ignored — handlers should
    raise on failure.
    `description` is a one-line summary intended for UI dropdowns
    and logs. Built-in modes have stable strings; plugin modes set
    their own.
    `is_builtin` distinguishes modes shipped with bpp from
    plugin-registered modes. Built-ins are never overwritten by
    `register_export_mode()` without `replace=True`.
    """

    name: str
    handler: Callable[[str, str], None]
    description: str = ""
    is_builtin: bool = False


class ExportModeRegistry:
    """Public registry for export modes (built-in + plugin-registered).

    Plugins call `register_export_mode("my_mode", handler)` from their
    setup() and then bpp dispatches `mode="my_mode"` through their
    handler in the export loop.

    The handler contract is `(src: str, dest: str) -> None`:
      - `src` is the original photo on disk.
      - `dest` is the fully-resolved per-photo destination path
        (the loop has already created the parent dir and applied
        safe_join). Use it as a hint; plugins are free to upload to
        a remote bucket / write a manifest entry / etc., as long as
        a meaningful artefact ends up at or near `dest`.
      - Raise on failure. The export loop catches and records each
        per-photo failure independently — one bad photo doesn't abort
        the run.

    Mode dispatch in `export_selected()` falls back to `copy` when an
    unknown mode is requested, preserving legacy behaviour for typos.

    Note on processing: image-processing paths (mode=`copy` with a
    non-`original` format or a max_size cap) write the processed bytes
    via PIL BEFORE the handler is consulted. Plugin modes therefore
    receive the ORIGINAL bytes, not processed copies. If your mode
    needs format conversion / resizing, do it inside your handler.
    """

    _modes: ClassVar[dict[str, ExportMode]] = {}

    @classmethod
    def register(cls, mode: ExportMode, *, replace: bool = False) -> None:
        existing = cls._modes.get(mode.name)
        if existing is not None and not replace:
            if existing.is_builtin:
                raise ValueError(
                    f"Cannot register export mode {mode.name!r}: "
                    "name is reserved for a built-in (pass replace=True "
                    "to override, but expect tests to scream)"
                )
            if existing.handler is not mode.handler:
                raise ValueError(
                    f"Export mode {mode.name!r} already registered with a "
                    "different handler (pass replace=True if intentional)"
                )
        cls._modes[mode.name] = mode

    @classmethod
    def get(cls, name: str) -> ExportMode | None:
        return cls._modes.get(name)

    @classmethod
    def all(cls) -> list[ExportMode]:
        return list(cls._modes.values())

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._modes.keys())

    @classmethod
    def _reset_for_tests(cls) -> None:
        """Drop every plugin-registered mode; keep built-ins. Test-only."""
        for name in list(cls._modes.keys()):
            if not cls._modes[name].is_builtin:
                del cls._modes[name]


def register_export_mode(
    name: str,
    handler: Callable[[str, str], None],
    *,
    description: str = "",
    replace: bool = False,
) -> None:
    """Public plugin entry point — add a custom export mode.

    See `ExportModeRegistry` for the handler contract. Plugins must
    use a plugin-prefixed name (e.g. `myplugin_s3`) to avoid
    colliding with built-ins. Built-in mode names: copy, hardlink,
    symlink, zip.
    """
    if name in {"copy", "hardlink", "symlink", "zip"} and not replace:
        raise ValueError(
            f"Export mode {name!r} is reserved for a built-in. Plugins "
            "must use a prefixed name like 'myplugin_<mode>' to avoid "
            "namespace collisions."
        )
    ExportModeRegistry.register(
        ExportMode(name=name, handler=handler, description=description),
        replace=replace,
    )


# Built-in modes registered at import time. Plugins extend the same
# registry from their setup() callable.
ExportModeRegistry.register(
    ExportMode(
        name="copy",
        handler=_export_copy,
        description="Copy photo bytes (default).",
        is_builtin=True,
    )
)
ExportModeRegistry.register(
    ExportMode(
        name="hardlink",
        handler=_export_hardlink,
        description="Hard-link to the original (zero disk usage; same filesystem only).",
        is_builtin=True,
    )
)
ExportModeRegistry.register(
    ExportMode(
        name="symlink",
        handler=_export_symlink,
        description="Symlink to the original (cross-filesystem, but breaks if source moves).",
        is_builtin=True,
    )
)
ExportModeRegistry.register(
    # Bundle mode — handled by export_selected() (see bundle_selected_zip);
    # the handler is the copy fallback and is never dispatched per-photo.
    ExportMode(
        name="zip",
        handler=_export_copy,
        description="Bundle the selected photos into a single .zip file.",
        is_builtin=True,
    )
)


# Backward-compat alias. Old code paths and tests may still iterate
# `_EXPORT_MODES`; expose a snapshot dict that mirrors the registry.
# New code uses `ExportModeRegistry.get(name)`.
_EXPORT_MODES: dict[str, Callable[[str, str], None]] = {
    m.name: m.handler for m in ExportModeRegistry.all()
}
