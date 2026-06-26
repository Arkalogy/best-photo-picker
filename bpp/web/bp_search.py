"""Search blueprint: universal search across photos, albums, people, dates, and CLIP."""

from __future__ import annotations

import calendar
import contextlib

import numpy as np
from flask import Blueprint, Response, jsonify, request

from bpp.constants import active_photo_sql
from bpp.db.albums import list_albums
from bpp.db.clip import get_clip_embedding_count
from bpp.db.photos import PHOTO_COLS_SLIM
from bpp.errors import ValidationError
from bpp.scoring.clip_embed import (
    compute_text_embedding,
)
from bpp.scoring.clip_embed import (
    is_available as clip_is_available,
)
from bpp.scoring.clip_embed import (
    text_is_available as clip_text_is_available,
)
from bpp.web.state import get_ctx

bp = Blueprint("search", __name__)


def _escape_like(s: str) -> str:
    """Escape LIKE wildcards so user input is treated as literal."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Month name -> number mapping for date search
_MONTH_MAP = {name.lower(): num for num, name in enumerate(calendar.month_name) if num}
_MONTH_ABBR_MAP = {name.lower(): num for num, name in enumerate(calendar.month_abbr) if num}

# Score qualifier keywords
_SCORE_QUALIFIERS = {
    "great": (0.7, 1.0),
    "good": (0.5, 0.7),
    "fair": (0.3, 0.5),
    "low": (0.0, 0.3),
    "low quality": (0.0, 0.3),
}

_MAX_PHOTO_RESULTS = 50
_MAX_SEMANTIC_RESULTS = 20
_SEMANTIC_MIN_SIMILARITY = 0.22


@bp.get("/api/v1/search")
def api_search() -> tuple[Response, int]:
    """Universal search across photos, albums, people, dates, tags, and
    CLIP semantic results.

    Query param ``q`` drives all matchers: score qualifiers ("great",
    "good", ...), filename LIKE, album/person/tag name match, parsed
    date queries (year, month, "Month YYYY"), and CLIP text-to-image
    similarity. The response also reports ``clip_status`` so the UI
    can prompt the user when CLIP isn't ready."""
    q = request.args.get("q", "").strip()
    if not q:
        raise ValidationError("Query parameter 'q' is required", field="q")

    ctx = get_ctx()
    conn = ctx.get_conn()
    q_lower = q.lower()

    photos_results = []
    albums_results = []
    people_results = []
    dates_results = []

    # --- Score qualifier search ---
    score_range = _match_score_qualifier(q_lower)
    if score_range:
        lo, hi = score_range
        rows = conn.execute(
            f"SELECT {PHOTO_COLS_SLIM} FROM photos "
            f"WHERE {active_photo_sql()} "
            "AND aggregate_score >= ? AND aggregate_score < ? "
            "ORDER BY aggregate_score DESC LIMIT ?",
            (lo, hi, _MAX_PHOTO_RESULTS),
        ).fetchall()
        photos_results = [ctx.build_photo_dict(dict(r)) for r in rows]
        return jsonify(
            {
                "photos": photos_results,
                "albums": albums_results,
                "people": people_results,
                "dates": dates_results,
                "semantic": [],
            }
        ), 200

    # --- Photo filename search ---
    q_escaped = _escape_like(q_lower)
    rows = conn.execute(
        f"SELECT {PHOTO_COLS_SLIM} FROM photos "
        f"WHERE {active_photo_sql()} "
        r"AND LOWER(original_filename) LIKE ? ESCAPE '\' "
        "ORDER BY date DESC LIMIT ?",
        (f"%{q_escaped}%", _MAX_PHOTO_RESULTS),
    ).fetchall()
    photos_results = [ctx.build_photo_dict(dict(r)) for r in rows]

    # --- Album name search ---
    # Internal/system albums opt out via SmartAlbumRegistry(searchable=False);
    # person hits route to the People list via result_bucket="people". Both
    # were hardcoded here before (Review 2026-06-17) — now registry-driven so
    # a plugin album type can hide from search / declare its own bucket.
    from bpp.db.smart_albums import SmartAlbumRegistry

    albums = list_albums(conn)
    for album in albums:
        atype = album["album_type"]
        if not SmartAlbumRegistry.is_searchable(atype):
            continue
        if q_lower not in album["name"].lower():
            continue
        if SmartAlbumRegistry.get_result_bucket(atype) == "people":
            people_results.append(
                {
                    "album_id": album["id"],
                    "name": album["name"],
                    "photo_count": album.get("photo_count", 0),
                }
            )
        else:
            albums_results.append(
                {
                    "id": album["id"],
                    "name": album["name"],
                    "photo_count": album.get("photo_count", 0),
                }
            )

    # --- Tag search ---
    tag_results = _search_tags(conn, ctx, q_lower)

    # --- Date search (month names, years) ---
    date_filter = _parse_date_query(q_lower)
    if date_filter:
        dates_results = date_filter
        # Also add matching photos by date if no filename matches found
        if not photos_results:
            photos_results = _search_photos_by_date(conn, ctx, date_filter)

    # --- CLIP semantic search ---
    semantic_results = _semantic_search(conn, ctx, q)

    # Report CLIP readiness so the UI can show helpful guidance
    clip_ready = clip_is_available() and clip_text_is_available()
    clip_count = 0
    if clip_ready:
        with contextlib.suppress(Exception):
            clip_count = get_clip_embedding_count(conn)

    return jsonify(
        {
            "photos": photos_results,
            "albums": albums_results,
            "people": people_results,
            "dates": dates_results,
            "tags": tag_results,
            "semantic": semantic_results,
            "clip_status": {
                "ready": clip_ready and clip_count > 0,
                "models_available": clip_ready,
                "embedding_count": clip_count,
            },
        }
    ), 200


def _search_tags(conn, ctx, q_lower: str) -> list[dict]:
    """Search tags by name and return matching photos."""
    rows = conn.execute(
        "SELECT t.id, t.name, COUNT(pt.photo_id) as photo_count "
        "FROM tags t JOIN photo_tags pt ON pt.tag_id = t.id "
        "JOIN photos p ON p.id = pt.photo_id "
        f"WHERE LOWER(t.name) LIKE ? ESCAPE '\\' AND {active_photo_sql('p')} "
        "GROUP BY t.id ORDER BY photo_count DESC LIMIT 10",
        (f"%{_escape_like(q_lower)}%",),
    ).fetchall()
    return [{"id": r[0], "name": r[1], "photo_count": r[2]} for r in rows]


def _semantic_search(conn, ctx, query: str) -> list[dict]:
    """Run CLIP text-to-image semantic search."""
    text_emb = compute_text_embedding(query)
    if text_emb is None:
        return []

    # Load CLIP embeddings (photo_id -> embedding)
    embeddings = ctx.load_clip_embeddings()
    if not embeddings:
        return []

    # Use pre-computed stacked matrix from cache (avoids ~200MB alloc per request)
    with ctx.lock:
        emb_matrix = ctx.caches.clip_cache.get("matrix")
        photo_ids = ctx.caches.clip_cache.get("matrix_ids")
    if emb_matrix is None or photo_ids is None:
        photo_ids = [pid for pid in embeddings if pid in embeddings]
        if not photo_ids:
            return []
        emb_matrix = np.stack([embeddings[pid] for pid in photo_ids])
    similarities = emb_matrix @ text_emb  # dot product (both L2-normalized)

    # Filter by minimum similarity and get top-K
    mask = similarities >= _SEMANTIC_MIN_SIMILARITY
    if not mask.any():
        return []

    # Sort by similarity descending
    order = np.argsort(similarities)[::-1][:_MAX_SEMANTIC_RESULTS]

    # Fetch photo rows for top results
    top_ids = [photo_ids[i] for i in order if similarities[i] >= _SEMANTIC_MIN_SIMILARITY]
    min_sim = _SEMANTIC_MIN_SIMILARITY
    top_sims = [float(similarities[i]) for i in order if similarities[i] >= min_sim]
    top_ids = top_ids[:_MAX_SEMANTIC_RESULTS]
    top_sims = top_sims[:_MAX_SEMANTIC_RESULTS]

    if not top_ids:
        return []

    placeholders = ", ".join(["?"] * len(top_ids))
    rows = conn.execute(
        f"SELECT {PHOTO_COLS_SLIM} FROM photos WHERE id IN ({placeholders}) "
        f"AND {active_photo_sql()}",
        top_ids,
    ).fetchall()
    row_map = {r["id"]: dict(r) for r in rows}

    results = []
    for pid, sim in zip(top_ids, top_sims, strict=True):
        if pid not in row_map:
            continue
        photo = ctx.build_photo_dict(row_map[pid])
        photo["similarity"] = round(sim, 4)
        results.append(photo)
    return results


def _search_photos_by_date(conn, ctx, date_filters: list[dict]) -> list[dict]:
    """Query photos matching parsed date filters."""
    results = []
    for df in date_filters:
        if "date_month" in df:
            # Exact year-month match
            rows = conn.execute(
                f"SELECT {PHOTO_COLS_SLIM} FROM photos "
                f"WHERE {active_photo_sql()} "
                "AND date_month=? ORDER BY date DESC LIMIT ?",
                (df["date_month"], _MAX_PHOTO_RESULTS),
            ).fetchall()
        elif "year" in df and "month" not in df:
            # Year-only match
            like = f"{df['year']}-%"
            rows = conn.execute(
                f"SELECT {PHOTO_COLS_SLIM} FROM photos "
                f"WHERE {active_photo_sql()} "
                "AND date LIKE ? ORDER BY date DESC LIMIT ?",
                (like, _MAX_PHOTO_RESULTS),
            ).fetchall()
        elif "month" in df:
            # Month-only match (any year)
            like = f"%-{df['month']:02d}-%"
            rows = conn.execute(
                f"SELECT {PHOTO_COLS_SLIM} FROM photos "
                f"WHERE {active_photo_sql()} "
                "AND date_month LIKE ? ORDER BY date DESC LIMIT ?",
                (like, _MAX_PHOTO_RESULTS),
            ).fetchall()
        else:
            continue
        results.extend(ctx.build_photo_dict(dict(r)) for r in rows)
    # Deduplicate by filepath and cap
    seen = set()
    unique = []
    for p in results:
        if p["filepath"] not in seen:
            seen.add(p["filepath"])
            unique.append(p)
        if len(unique) >= _MAX_PHOTO_RESULTS:
            break
    return unique


def _match_score_qualifier(q: str) -> tuple[float, float] | None:
    """Check if query matches a score qualifier keyword."""
    for keyword, score_range in _SCORE_QUALIFIERS.items():
        if q == keyword:
            return score_range
    return None


def _parse_date_query(q: str) -> list[dict]:
    """Parse a date query and return matching date filters.

    Supports: "2024", "January", "Jan", "January 2024", "Jan 2024"
    """
    results = []
    parts = q.split()

    # Try full month name or abbreviation
    month_num = _MONTH_MAP.get(q) or _MONTH_ABBR_MAP.get(q)
    if month_num:
        month_name = calendar.month_name[month_num]
        results.append({"label": month_name, "month": month_num})
        return results

    # Try "Month Year" or "Year"
    if len(parts) == 2:
        month_num = _MONTH_MAP.get(parts[0]) or _MONTH_ABBR_MAP.get(parts[0])
        if month_num and parts[1].isdigit() and len(parts[1]) == 4:
            year = int(parts[1])
            month_name = calendar.month_name[month_num]
            results.append(
                {
                    "label": f"{month_name} {year}",
                    "month": month_num,
                    "year": year,
                    "date_month": f"{year}-{month_num:02d}",
                }
            )
            return results

    # Try bare year
    if q.isdigit() and len(q) == 4:
        year = int(q)
        if 1900 <= year <= 2100:
            results.append({"label": str(year), "year": year})

    return results
