"""Two faces of one photo in one cluster: one photo cannot show a person
twice, so they are either one face detected twice or two people grouped
by mistake. Run after a chunk of new faces is grouped (phase 6)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

import numpy as np

from bpp.db.face_cluster_ids import highest_cluster_id
from bpp.db.sql_batches import rows_in
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Boxes in one photo overlapping at least this much are one face found twice.
SAME_FACE_IOU = 0.5

#: Faces of the cluster from other photos that decide which face belongs.
_REFERENCE_FACES = 10


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _reference(conn: sqlite3.Connection, cid: int, photo_id: int) -> np.ndarray | None:
    """The cluster's best faces from its other photos, or None if it has none."""
    blobs = conn.execute(
        "SELECT embedding FROM face_embeddings WHERE cluster_id = ? AND photo_id != ?"
        " ORDER BY quality DESC, id LIMIT ?",
        (cid, photo_id, _REFERENCE_FACES),
    ).fetchall()
    vecs = [np.frombuffer(b[0], dtype=np.float32) for b in blobs if b[0]]
    vecs = [v for v in vecs if np.isfinite(v).all()]
    if not vecs or len({len(v) for v in vecs}) != 1:
        return None
    return np.stack(vecs).astype(np.float64)


def _keeper(conn: sqlite3.Connection, cid: int, photo_id: int, faces: list[tuple]) -> int:
    """Index in ``faces`` of the face that keeps the cluster: one the user
    placed by hand, else the one nearest the cluster's faces in its other
    photos (the person the cluster is about), else the earliest row."""
    for i, face in enumerate(faces):
        if face[2]:
            return i
    ref = _reference(conn, cid, photo_id)
    if ref is None:
        return 0
    best, best_d = 0, np.inf
    for i, face in enumerate(faces):
        emb = np.frombuffer(face[1], dtype=np.float32) if face[1] else None
        if emb is None or len(emb) != ref.shape[1] or not np.isfinite(emb).all():
            continue
        d = float(np.linalg.norm(ref - emb.astype(np.float64), axis=1).mean())
        if d < best_d:
            best, best_d = i, d
    return best


def part_same_photo_faces(conn: sqlite3.Connection, photo_ids: Iterable[int]) -> None:
    """For ``photo_ids`` (the chunk just grouped): of two faces of one photo
    in one cluster, the face that belongs keeps it (``_keeper``); a face
    whose box overlaps the keeper's is a double detection and is dropped,
    any other gets a cluster of its own, so a person grouped by mistake
    never loses their face. Commits when it changed anything."""
    rows = rows_in(
        conn,
        "SELECT id, photo_id, cluster_id, embedding, user_confirmed,"
        " bbox_x, bbox_y, bbox_w, bbox_h FROM face_embeddings"
        " WHERE cluster_id >= 0 AND photo_id IN ({ph})",
        list(photo_ids),
    )
    groups: dict[tuple[int, int], list[tuple]] = {}
    for fid, pid, cid, blob, confirmed, *box in sorted(rows, key=lambda r: r[0]):
        groups.setdefault((pid, cid), []).append((fid, blob, confirmed, tuple(box)))
    drop: list[tuple[int]] = []
    part: list[int] = []
    for (pid, cid), faces in groups.items():
        if len(faces) < 2:
            continue
        k = _keeper(conn, cid, pid, faces)
        kept_box = faces[k][3]
        for i, (fid, _blob, confirmed, box) in enumerate(faces):
            if i == k:
                continue
            same = None not in box and None not in kept_box and _iou(kept_box, box) >= SAME_FACE_IOU
            if same and not confirmed:
                drop.append((fid,))
            else:
                part.append(fid)
    if drop:
        conn.executemany("DELETE FROM face_embeddings WHERE id = ?", drop)
    if part:
        first = highest_cluster_id(conn) + 1
        conn.executemany(
            "UPDATE face_embeddings SET cluster_id = ? WHERE id = ?",
            [(first + i, fid) for i, fid in enumerate(part)],
        )
    if drop or part:
        conn.commit()
        log.info(
            "Same photo, same cluster: dropped %d double detections, parted %d faces",
            len(drop),
            len(part),
        )
