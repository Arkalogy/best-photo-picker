"""`bpp pick` — power-user one-liner: load analysis, score, select, export.

Extracted from bpp.commands during the v0.1 cleanup. Re-exported
from `bpp.commands` for backwards compatibility with the CLI.
"""

from __future__ import annotations

import argparse
import os

from bpp.utils.logging import get_logger


def do_pick(args: argparse.Namespace) -> int:
    """Power-user one-liner: load analysis, score, select, export."""
    import json
    import sys

    from bpp.config import DEFAULTS
    from bpp.db.connection import close_all_connections, get_db, init_db
    from bpp.db.library import get_library_dirs
    from bpp.db.photos import get_all_photos
    from bpp.plugins import load_plugin_entry_points
    from bpp.selection.choose import choose
    from bpp.utils.json_utils import safe_json_loads

    # ensure plugin scorers / config fields are registered
    # before we read the scoring registry.
    load_plugin_entry_points()

    log = get_logger(__name__)

    library_path = os.path.abspath(args.library)
    if not os.path.isdir(library_path):
        print(f"Error: library path does not exist: {library_path}", file=sys.stderr)
        return 1

    db_path = os.path.join(get_library_dirs(library_path)["data"], "photopicker.db")
    if not os.path.exists(db_path):
        print(f"Error: no database found at {db_path}", file=sys.stderr)
        return 1

    try:
        from bpp.db.connection import backup_db

        backup_db(db_path)
        init_db(db_path)
        conn = get_db(db_path)

        # Load analyzed photos from DB
        photos = get_all_photos(conn)
        analyzed = [p for p in photos if p.get("aggregate_score") is not None]
        if not analyzed:
            print("Error: no analyzed photos found. Run analysis first.", file=sys.stderr)
            return 1

        config = dict(DEFAULTS)

        # Face boost: look up cluster IDs by name from smart_person albums
        boost_face_names = getattr(args, "boost_face", [])
        selected_faces: list[int] = []
        face_cluster_map: dict[str, list[int]] = {}

        if boost_face_names:
            from bpp.web.face_worker import load_face_cluster_map

            face_cluster_map = load_face_cluster_map(conn)

            # Batch-fetch all named-person albums in one IN-clause query;
            # missing names then surface in a single error message.
            placeholders = ",".join("?" for _ in boost_face_names)
            rows = conn.execute(
                f"SELECT name, rule_json FROM albums "
                f"WHERE album_type='smart_person' AND name IN ({placeholders})",
                list(boost_face_names),
            ).fetchall()
            rules_by_name = {r[0]: r[1] for r in rows}
            missing = [n for n in boost_face_names if n not in rules_by_name]
            if missing:
                print(
                    f"Error: no face cluster named {', '.join(repr(n) for n in missing)}. "
                    "Check named people in the web UI.",
                    file=sys.stderr,
                )
                return 1
            for name in boost_face_names:
                rule = safe_json_loads(rules_by_name[name], {}, context="face boost rule")
                cluster_id = rule.get("cluster_id")
                if cluster_id is not None:
                    selected_faces.append(cluster_id)

            # Apply face boost to aggregate scores
            face_boost = config.get("face_selection_boost", 0.15)
            selected_set = set(selected_faces)
            for item in analyzed:
                fp = item["filepath"]
                item_clusters = set(face_cluster_map.get(fp, []))
                overlap = item_clusters & selected_set
                if overlap:
                    boost = face_boost * min(len(overlap), 3) / 3
                    item["aggregate_score"] = min(1.0, item.get("aggregate_score", 0) + boost)

        # Select top K
        selected = choose(analyzed, k=args.top, config=config, seed=getattr(args, "seed", 42))

        # Output
        if getattr(args, "json", False):
            output = [
                {
                    "filepath": s["filepath"],
                    "aggregate_score": round(s.get("aggregate_score", 0), 4),
                    "blur_score": round(s.get("blur_score", 0), 4),
                    "exposure_score": round(s.get("exposure_score", 0), 4),
                    "face_score": round(s.get("face_score", 0), 4),
                    "composition_score": round(s.get("composition_score", 0), 4),
                }
                for s in selected
            ]
            print(json.dumps(output, indent=2))
        elif getattr(args, "paths_only", False):
            for s in selected:
                print(s["filepath"])
        else:
            # Human-readable table
            print(f"{'#':<4} {'Score':>7} {'Blur':>6} {'Expo':>6} {'Face':>6} {'Comp':>6}  Path")
            print("-" * 80)
            for i, s in enumerate(selected, 1):
                print(
                    f"{i:<4} {s.get('aggregate_score', 0):>7.4f} "
                    f"{s.get('blur_score', 0):>6.3f} "
                    f"{s.get('exposure_score', 0):>6.3f} "
                    f"{s.get('face_score', 0):>6.3f} "
                    f"{s.get('composition_score', 0):>6.3f}  "
                    f"{s['filepath']}"
                )

        # Export if --out specified and not --dry-run
        out_dir = getattr(args, "out", None)
        dry_run = getattr(args, "dry_run", False)
        if out_dir and not dry_run:
            from bpp.output.export import export_selected

            quality_map = {
                "original": ("original", None, 85),
                "high": ("jpeg", None, 92),
                "medium": ("jpeg", 2048, 85),
                "low": ("jpeg", 1024, 70),
            }
            fmt, max_size, quality = quality_map.get(args.quality, ("original", None, 85))

            out_dir = os.path.abspath(out_dir)
            export_selected(
                selected,
                analysis=analyzed,
                outdir=out_dir,
                fmt=fmt,
                max_size=max_size,
                quality=quality,
            )
            log.info("Exported %d photos to %s", len(selected), out_dir)
        elif out_dir and dry_run:
            log.info("[dry-run] Would export %d photos to %s", len(selected), out_dir)

        return 0
    finally:
        close_all_connections()
