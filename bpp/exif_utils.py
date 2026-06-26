"""EXIF metadata extraction utilities."""

from __future__ import annotations

import datetime
import os
from typing import Any

from PIL import Image
from PIL.ExifTags import IFD
from PIL.ExifTags import Base as ExifBase

from bpp.utils.logging import get_logger
from bpp.utils.retry import retry_io

log = get_logger(__name__)


def _format_shutter_speed(val: float) -> str:
    """Format exposure time as a human-readable string."""
    if val >= 1:
        return f"{val:.1f}s" if val != int(val) else f"{int(val)}s"
    if val <= 0:
        return "unknown"
    recip = round(1.0 / val)
    return f"1/{recip}"


def _parse_gps_coord(coord: Any, ref: str | None) -> float | None:
    """Convert EXIF GPS coordinate to decimal degrees."""
    if coord is None or ref is None:
        return None
    try:
        if isinstance(coord, (tuple, list)) and len(coord) == 3:
            deg = float(coord[0])
            minutes = float(coord[1])
            sec = float(coord[2])
            decimal = deg + minutes / 60.0 + sec / 3600.0
        else:
            decimal = float(coord)
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 6)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _safe_float(val: Any) -> float | None:
    """Safely convert an EXIF value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> int | None:
    """Safely convert an EXIF value to int."""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError, OverflowError):
        return None


def extract_exif_metadata(filepath: str) -> dict[str, Any]:
    """Extract common EXIF metadata from an image file.

    Returns a dict with available fields:
      width, height, camera_make, camera_model, lens, iso,
      aperture, shutter_speed, focal_length, gps_lat, gps_lon
    Empty dict on error.
    """
    try:
        img = retry_io(Image.open, filepath, label="exif_meta_open")
    except Exception:
        return {}

    result: dict[str, Any] = {}
    try:
        with img:
            result["width"] = img.width
            result["height"] = img.height

            exif = img.getexif()
            if not exif:
                return result

            # Camera
            make = exif.get(ExifBase.Make)
            if make and isinstance(make, str):
                result["camera_make"] = make.strip()
            model = exif.get(ExifBase.Model)
            if model and isinstance(model, str):
                result["camera_model"] = model.strip()

            # Lens (tag 42036)
            lens = exif.get(ExifBase.LensModel)
            if lens and isinstance(lens, str):
                result["lens"] = lens.strip()

            # ISO
            iso = _safe_int(exif.get(ExifBase.ISOSpeedRatings))
            if iso is not None:
                result["iso"] = iso

            # Aperture (F-number)
            fnum = _safe_float(exif.get(ExifBase.FNumber))
            if fnum is not None:
                result["aperture"] = round(fnum, 1)

            # Shutter speed
            exp_time = _safe_float(exif.get(ExifBase.ExposureTime))
            if exp_time is not None and exp_time > 0:
                result["shutter_speed"] = _format_shutter_speed(exp_time)

            # Focal length
            fl = _safe_float(exif.get(ExifBase.FocalLength))
            if fl is not None:
                result["focal_length"] = round(fl, 1)

            # GPS
            try:
                gps_ifd = exif.get_ifd(IFD.GPSInfo)
                if gps_ifd:
                    lat = _parse_gps_coord(gps_ifd.get(2), gps_ifd.get(1))
                    lon = _parse_gps_coord(gps_ifd.get(4), gps_ifd.get(3))
                    if lat is not None:
                        result["gps_lat"] = lat
                    if lon is not None:
                        result["gps_lon"] = lon
            except Exception:
                log.debug("GPS extraction failed for %s", filepath)

    except Exception as e:
        log.debug("EXIF metadata extraction failed for %s: %s", filepath, e)

    return result


def extract_exif_date(filepath: str) -> datetime.datetime | None:
    """Extract the original date/time from EXIF data. Returns None if unavailable."""
    try:
        img = retry_io(Image.open, filepath, label="exif_open")
        with img:
            exif = img.getexif()
            if not exif:
                return None
            # DateTimeOriginal (36867) is most reliable
            dt_str = exif.get(ExifBase.DateTimeOriginal) or exif.get(ExifBase.DateTime)
            if dt_str and isinstance(dt_str, str):
                return datetime.datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
    except Exception as e:
        log.debug("EXIF extraction failed for %s: %s", filepath, e)
    return None


def get_file_date(filepath: str) -> datetime.datetime:
    """Fallback: use file modification time if EXIF date is unavailable."""
    mtime = retry_io(os.path.getmtime, filepath, label="file_mtime")
    return datetime.datetime.fromtimestamp(mtime)


def get_date(filepath: str) -> datetime.datetime:
    """Get the best available date for an image (EXIF preferred, mtime fallback)."""
    return extract_exif_date(filepath) or get_file_date(filepath)
