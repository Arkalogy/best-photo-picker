"""CRUD operations for photo_edits table (non-destructive edits)."""

from __future__ import annotations

import json
import sqlite3

from bpp.utils.json_utils import safe_json_loads


def save_photo_edits(
    conn: sqlite3.Connection,
    photo_id: int,
    edits: dict,
) -> None:
    """Save or update edit parameters for a photo."""
    redeye_points = edits.get("redeye_points")
    redeye_json = json.dumps(redeye_points) if redeye_points else None

    conn.execute(
        """INSERT INTO photo_edits (photo_id, brightness, contrast, saturation, sharpness,
                                    crop_x, crop_y, crop_w, crop_h,
                                    rotation, flip_h, flip_v,
                                    warmth, highlights, shadows,
                                    vignette, grain, fade,
                                    redeye_json, filter_name,
                                    exposure, brilliance, black_point,
                                    vibrance, tint, definition, noise_reduction,
                                    straighten, perspective_v, perspective_h,
                                    auto_enhanced, modified_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(photo_id) DO UPDATE SET
             brightness=excluded.brightness,
             contrast=excluded.contrast,
             saturation=excluded.saturation,
             sharpness=excluded.sharpness,
             crop_x=excluded.crop_x,
             crop_y=excluded.crop_y,
             crop_w=excluded.crop_w,
             crop_h=excluded.crop_h,
             rotation=excluded.rotation,
             flip_h=excluded.flip_h,
             flip_v=excluded.flip_v,
             warmth=excluded.warmth,
             highlights=excluded.highlights,
             shadows=excluded.shadows,
             vignette=excluded.vignette,
             grain=excluded.grain,
             fade=excluded.fade,
             redeye_json=excluded.redeye_json,
             filter_name=excluded.filter_name,
             exposure=excluded.exposure,
             brilliance=excluded.brilliance,
             black_point=excluded.black_point,
             vibrance=excluded.vibrance,
             tint=excluded.tint,
             definition=excluded.definition,
             noise_reduction=excluded.noise_reduction,
             straighten=excluded.straighten,
             perspective_v=excluded.perspective_v,
             perspective_h=excluded.perspective_h,
             auto_enhanced=excluded.auto_enhanced,
             modified_at=datetime('now')
        """,
        (
            photo_id,
            edits.get("brightness", 1.0),
            edits.get("contrast", 1.0),
            edits.get("saturation", 1.0),
            edits.get("sharpness", 1.0),
            edits.get("crop_x"),
            edits.get("crop_y"),
            edits.get("crop_w"),
            edits.get("crop_h"),
            edits.get("rotation", 0),
            bool(edits.get("flip_h", False)),
            bool(edits.get("flip_v", False)),
            edits.get("warmth", 0.0),
            edits.get("highlights", 0.0),
            edits.get("shadows", 0.0),
            edits.get("vignette", 0.0),
            edits.get("grain", 0.0),
            edits.get("fade", 0.0),
            redeye_json,
            edits.get("filter_name"),
            edits.get("exposure", 0.0),
            edits.get("brilliance", 0.0),
            edits.get("black_point", 0.0),
            edits.get("vibrance", 0.0),
            edits.get("tint", 0.0),
            edits.get("definition", 0.0),
            edits.get("noise_reduction", 0.0),
            edits.get("straighten", 0.0),
            edits.get("perspective_v", 0.0),
            edits.get("perspective_h", 0.0),
            bool(edits.get("auto_enhanced", False)),
        ),
    )
    conn.commit()


def get_photo_edits(conn: sqlite3.Connection, photo_id: int) -> dict | None:
    """Return edit params dict for a photo, or None if unedited."""
    row = conn.execute(
        """SELECT brightness, contrast, saturation, sharpness,
                  crop_x, crop_y, crop_w, crop_h,
                  rotation, flip_h, flip_v,
                  warmth, highlights, shadows,
                  vignette, grain, fade,
                  redeye_json, filter_name,
                  exposure, brilliance, black_point,
                  vibrance, tint, definition, noise_reduction,
                  straighten, perspective_v, perspective_h
           FROM photo_edits WHERE photo_id=?""",
        (photo_id,),
    ).fetchone()
    if row is None:
        return None

    redeye_raw = row["redeye_json"]
    redeye_points = safe_json_loads(redeye_raw, context="redeye edits")

    return {
        "brightness": row["brightness"],
        "contrast": row["contrast"],
        "saturation": row["saturation"],
        "sharpness": row["sharpness"],
        "crop_x": row["crop_x"],
        "crop_y": row["crop_y"],
        "crop_w": row["crop_w"],
        "crop_h": row["crop_h"],
        "rotation": row["rotation"] or 0,
        "flip_h": bool(row["flip_h"]),
        "flip_v": bool(row["flip_v"]),
        "warmth": row["warmth"] or 0.0,
        "highlights": row["highlights"] or 0.0,
        "shadows": row["shadows"] or 0.0,
        "vignette": row["vignette"] or 0.0,
        "grain": row["grain"] or 0.0,
        "fade": row["fade"] or 0.0,
        "redeye_points": redeye_points,
        "filter_name": row["filter_name"],
        "exposure": row["exposure"] or 0.0,
        "brilliance": row["brilliance"] or 0.0,
        "black_point": row["black_point"] or 0.0,
        "vibrance": row["vibrance"] or 0.0,
        "tint": row["tint"] or 0.0,
        "definition": row["definition"] or 0.0,
        "noise_reduction": row["noise_reduction"] or 0.0,
        "straighten": row["straighten"] or 0.0,
        "perspective_v": row["perspective_v"] or 0.0,
        "perspective_h": row["perspective_h"] or 0.0,
    }


def reset_photo_edits(conn: sqlite3.Connection, photo_id: int) -> int:
    """Remove edits for a photo. Returns number of rows deleted."""
    cur = conn.execute("DELETE FROM photo_edits WHERE photo_id=?", (photo_id,))
    conn.commit()
    return cur.rowcount


def has_edits(conn: sqlite3.Connection, photo_id: int) -> bool:
    """Check if a photo has any edits."""
    row = conn.execute("SELECT 1 FROM photo_edits WHERE photo_id=? LIMIT 1", (photo_id,)).fetchone()
    return row is not None


def get_auto_enhanced_photo_ids(conn: sqlite3.Connection) -> set[int]:
    """Return set of photo IDs that were auto-enhanced."""
    rows = conn.execute("SELECT photo_id FROM photo_edits WHERE auto_enhanced = 1").fetchall()
    return {r["photo_id"] for r in rows}


def get_edited_photo_ids(conn: sqlite3.Connection) -> set[int]:
    """Return set of all photo IDs that have edits."""
    rows = conn.execute("SELECT photo_id FROM photo_edits").fetchall()
    return {r["photo_id"] for r in rows}
