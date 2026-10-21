"""Missing-photo recheck: the owner's forced scan of the whole library.

Split out of ``bp_core.py`` for the 500-line cap.
"""

from __future__ import annotations

import os

from flask import Blueprint, Response, jsonify

from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

bp = Blueprint("photos_missing", __name__)


@bp.post("/api/v1/photos/recheck-missing")
@requires_local_app
def api_recheck_missing() -> tuple[Response, int]:
    """Re-scan all missing photos and restore those whose files reappeared.

    Useful after NAS reconnect or external drive mount.
    """
    ctx = get_ctx()
    conn = ctx.get_conn()
    # The owner asked: scan everything, including marking files that are
    # gone (the periodic check refuses to flip most of a library at once).
    from bpp.db.photos_missing import scan_missing

    scan = scan_missing(conn, force=True)
    newly_missing = len(scan.newly_missing)
    # The owner's forced scan is the answer to a refused one — once it
    # has actually run to the end (storage may still be unreachable).
    if scan.completed:
        ctx.analysis_store.missing_scan_refused = None
    # Find photos currently marked missing
    rows = conn.execute("SELECT id, filepath FROM photos WHERE missing=1").fetchall()
    if not rows:
        return jsonify({"restored": 0, "still_missing": 0, "newly_missing": newly_missing}), 200

    from bpp.utils.retry import retry_io

    restored_ids: list[int] = []
    still_missing = 0
    for row in rows:
        pid, fp = row["id"], row["filepath"]
        try:
            found = retry_io(os.path.isfile, fp, label="recheck_missing")
        except OSError:
            found = False
        if found:
            restored_ids.append(pid)
        else:
            still_missing += 1
    # Single batched UPDATE instead of one round-trip per restored photo —
    # 50k-photo library reduces from N statements to one with an IN clause.
    if restored_ids:
        from bpp.db.sql_batches import in_batches

        for placeholders, batch in in_batches(restored_ids):
            conn.execute(
                f"UPDATE photos SET missing=0 WHERE id IN ({placeholders})",
                batch,
            )
        conn.commit()
    restored = len(restored_ids)

    if restored or newly_missing:
        # Reload analysis so the UI sees the change either way
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()

    return jsonify(
        {"restored": restored, "still_missing": still_missing, "newly_missing": newly_missing}
    ), 200
