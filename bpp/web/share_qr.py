"""QR code rendering + LAN share banner — extracted from share.py.

These functions render QR codes for the Settings → Share UI and
format the at-startup banner. They have no dependency on the rest of
share.py's auth / device / proxy machinery, so they live here to keep
share.py focused on the auth boundary.

Re-exported from share.py for backwards compatibility with existing
imports (`from bpp.web.share import render_qr_png`, etc.).
"""

from __future__ import annotations

import io

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _render_bpp_glyph(size: int = 220) -> object:
    """Render a small monochrome BPP glyph for QR center embedding.

    Black square with white "BPP" text, rounded corners. The point is
    to look intentional and match the QR's black/white palette — not
    to be a photographic icon. Generated on the fly so we don't bundle
    a separate raster.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = int(size * 0.18)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=(0, 0, 0, 255))

    font = None
    for face in ("Helvetica.ttc", "Helvetica.ttf", "Arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(face, int(size * 0.42))
            break
        except OSError:
            continue
    if font is None:
        # L2: PIL's bitmap default is small + jaggy at the QR center patch
        # size — surfaces visibly worse than any TrueType. Log a warning
        # so headless / minimal Linux deploys can be diagnosed (vs. a
        # silent quality drop the operator never sees).
        log.warning(
            "QR glyph: no TrueType font available — falling back to PIL bitmap"
            " default. Install one of Helvetica/Arial/DejaVuSans-Bold for crisper QR rendering."
        )
        font = ImageFont.load_default()

    text = "BPP"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # Compensate for the bbox having non-zero origin
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))
    return img


def render_qr_png(text: str, *, box_size: int = 12, border: int = 2) -> bytes:
    """Render a black-on-white QR with a small BPP glyph in the center.

    Styling:
    - Modules drawn with rounded corners (`RoundedModuleDrawer`) — softer
      than square pixels but still dense enough for camera scanning
    - Black on white. We tried BPP brand blue but it didn't blend with
      the dark Settings panel; black is what users expect a QR to look
      like and matches the high-contrast UI of the surrounding card.
    - A monochrome BPP glyph (black square, white text) embedded in the
      center — feels like part of the QR rather than a sticker
    - PNG output (PIL native; SVG support for embedded raster images
      in `qrcode` is hacky, and phones decode PNG faster anyway)
    """
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer

    qr = qrcode.QRCode(
        error_correction=ERROR_CORRECT_H,
        box_size=box_size,
        border=border,
    )
    qr.add_data(text)
    qr.make(fit=True)

    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        # "embeded" misspelling is the upstream qrcode API, not ours.
        embeded_image=_render_bpp_glyph(),
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_qr_svg(text: str, *, box_size: int = 10, border: int = 2) -> str:
    """Render a QR code as plain SVG (kept for backward compat with tests).

    The Settings → Share UI now uses `render_qr_png` for the branded
    look. This SVG variant is unstyled black-on-white — call sites
    should prefer the PNG version unless they specifically need vector
    output.
    """
    import qrcode
    from qrcode.image.svg import SvgPathImage

    img = qrcode.make(text, image_factory=SvgPathImage, box_size=box_size, border=border)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def format_share_banner(host_port: str) -> list[str]:
    """Format multi-line LAN share banner for log output.

    Takes a non-secret `host:port` string (e.g. ``192.168.1.5:5001``)
    so the banner is safe to log to disk and screenshot. The full
    tokenized share URL is intentionally NOT in the banner — it's a
    long-lived secret that would otherwise persist in server.log
    (5MB x 3 rotation = weeks of retention) and any cloud / support
    backup of the host. Operators copy the URL from the owner-only
    Settings → Share UI, which renders `/api/v1/share/info`.

    Returned as a list of lines so the caller picks the log level
    (WARNING is loud enough to stand out without being scary).
    """
    return [
        "",
        "=" * 70,
        "LAN SHARING ENABLED — server bound to 0.0.0.0",
        "Anyone on your local network with the share URL can access your photos.",
        "Only enable on networks you trust (home Wi-Fi, not coffee shops).",
        "",
        f"  Listening on:  http://{host_port}",
        "  Share URL:     copy from Settings → Share in the app",
        "                 (kept out of logs to avoid token leakage)",
        "",
        "=" * 70,
        "",
    ]
