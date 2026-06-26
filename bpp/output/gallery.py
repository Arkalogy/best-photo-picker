"""Generate static HTML gallery from selected photos."""

from __future__ import annotations

import os
from typing import Any

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

from bpp import APP_NAME
from bpp.constants import JPEG_QUALITY_THUMB, THUMBNAIL_MAX_SIZE
from bpp.utils.logging import get_logger
from bpp.utils.paths import safe_join
from bpp.utils.retry import retry_io

log = get_logger(__name__)

GALLERY_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(app_name)s Gallery</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #1a1a1a; color: #eee; padding: 20px; }
  h1 { text-align: center; margin-bottom: 20px; font-weight: 300; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
          gap: 16px; max-width: 1400px; margin: 0 auto; }
  .card { background: #2a2a2a; border-radius: 8px; overflow: hidden; }
  .card img { width: 100%%; height: 220px; object-fit: cover; display: block; }
  .card .info { padding: 10px; font-size: 13px; }
  .card .info .name { font-weight: 600; margin-bottom: 4px;
                      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card .info .meta { color: #999; }
  .card .info .score { color: #6c6; font-weight: 600; }
  .stats { text-align: center; margin: 20px 0; color: #888; font-size: 14px; }
</style>
</head>
<body>
<h1>%(app_name)s Gallery</h1>
<p class="stats">%(count)d photos selected</p>
<div class="grid">
%(cards)s
</div>
</body>
</html>
"""

CARD_TEMPLATE = """\
<div class="card">
  <img src="thumbnails/%(thumb)s" alt="%(name)s" loading="lazy">
  <div class="info">
    <div class="name">%(name)s</div>
    <div class="meta">%(date)s</div>
    <div class="score">Score: %(score).3f</div>
    <div class="meta">%(reason)s</div>
  </div>
</div>"""


def _make_thumbnail(src: str, dest: str, size: int = THUMBNAIL_MAX_SIZE) -> bool:
    from bpp.media_types import MediaKind, media_kind_from_path

    kind = media_kind_from_path(src)

    # Video files: extract a frame via OpenCV
    if kind is MediaKind.VIDEO:
        return _make_video_thumbnail(src, dest, size)

    try:
        if kind is MediaKind.RAW:
            from bpp.utils.raw import open_raw_as_pil

            img = open_raw_as_pil(src)
            if img is None:
                return False
        else:
            img = retry_io(Image.open, src, label="thumb_open")
            img = ImageOps.exif_transpose(img)
        img.thumbnail((size, size), Image.LANCZOS)
        img = img.convert("RGB")
        img.save(dest, "JPEG", quality=JPEG_QUALITY_THUMB)
        return True
    except Exception as e:
        log.debug("Thumbnail failed for %s: %s", src, e)
        return False


def _make_video_thumbnail(src: str, dest: str, size: int = THUMBNAIL_MAX_SIZE) -> bool:
    """Generate a JPEG thumbnail from a video file's middle frame."""
    from bpp.utils.video import extract_video_frame

    try:
        img = extract_video_frame(src, position=0.5)
        if img is None:
            return False
        img.thumbnail((size, size), Image.LANCZOS)
        img = img.convert("RGB")
        img.save(dest, "JPEG", quality=JPEG_QUALITY_THUMB)
        return True
    except Exception as e:
        log.debug("Video thumbnail failed for %s: %s", src, e)
        return False


def generate_gallery(selected: list[dict[str, Any]], outdir: str) -> None:
    """Generate gallery.html with thumbnails."""
    thumb_dir = os.path.join(outdir, "thumbnails")
    os.makedirs(thumb_dir, exist_ok=True)

    cards = []
    for i, item in enumerate(selected, 1):
        src = item["filepath"]
        name = os.path.basename(src)
        thumb_name = f"{i:03d}_{os.path.splitext(name)[0]}.jpg"
        thumb_path = safe_join(thumb_dir, thumb_name)

        if _make_thumbnail(src, thumb_path):
            card = CARD_TEMPLATE % {
                "thumb": thumb_name,
                "name": name,
                "date": item.get("date_day") or (item.get("date") or "")[:10],
                "score": item.get("aggregate_score", 0),
                "reason": item.get("selection_reason", ""),
            }
            cards.append(card)

    html = GALLERY_TEMPLATE % {
        "app_name": APP_NAME,
        "count": len(selected),
        "cards": "\n".join(cards),
    }

    gallery_path = os.path.join(outdir, "gallery.html")
    with open(gallery_path, "w") as f:
        f.write(html)

    log.info("Gallery written: %s (%d thumbnails)", gallery_path, len(cards))
