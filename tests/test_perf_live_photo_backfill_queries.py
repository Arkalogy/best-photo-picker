"""Live Photo sidecar tagging on startup must not pull every photo whose
name has an underscore (that is every iPhone photo) nor run one query
per folder."""

from __future__ import annotations

import sqlite3


class _Cursor:
    def __init__(self, cur, spy):
        self._cur, self._spy = cur, spy

    def fetchall(self):
        rows = self._cur.fetchall()
        self._spy.rows += len(rows)
        return rows

    def fetchone(self):
        row = self._cur.fetchone()
        self._spy.rows += 1 if row is not None else 0
        return row

    def __iter__(self):
        for row in self._cur:
            self._spy.rows += 1
            yield row

    @property
    def rowcount(self):
        return self._cur.rowcount


class _Spy:
    """Counts statements and rows pulled through a real connection."""

    def __init__(self, conn):
        self._c = conn
        self.statements = 0
        self.rows = 0

    def execute(self, sql, params=()):
        self.statements += 1
        return _Cursor(self._c.execute(sql, params), self)

    def executemany(self, sql, rows):
        self.statements += 1
        return self._c.executemany(sql, rows)

    def __getattr__(self, name):
        return getattr(self._c, name)


def _insert(conn: sqlite3.Connection, filepath: str) -> int:
    name = filepath.rsplit("/", 1)[1]
    conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
        "VALUES (?, ?, 1, 1.0)",
        (filepath, name),
    )
    return conn.execute("SELECT id FROM photos WHERE filepath=?", (filepath,)).fetchone()[0]


def test_backfill_pulls_only_sidecar_candidates_in_a_fixed_number_of_queries(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.live_photo import detect_and_link_live_photo_sidecars

    conn = init_db(str(tmp_path / "t.db"))
    parents, sidecars = [], []
    for d in range(6):
        parents.append(_insert(conn, f"/lib/{d}/IMG_4{d}14.HEIC"))
        sidecars.append(_insert(conn, f"/lib/{d}/IMG_4{d}14_1.HEIC"))
    for i in range(200):  # ordinary iPhone names: an underscore but no _N
        _insert(conn, f"/lib/x/IMG_{1000 + i}.HEIC")
    conn.commit()

    spy = _Spy(conn)
    assert detect_and_link_live_photo_sidecars(spy) == 6
    assert spy.statements <= 4, f"a query per folder: {spy.statements} statements"
    assert spy.rows <= 2 * len(sidecars) + 4, f"pulled {spy.rows} rows for 6 candidates"
    linked = conn.execute(
        "SELECT live_photo_parent_id FROM photos WHERE is_live_photo_sidecar = 1 ORDER BY id"
    ).fetchall()
    assert [r[0] for r in linked] == parents


def test_parents_with_non_ascii_names_still_link(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.live_photo import detect_and_link_live_photo_sidecars

    conn = init_db(str(tmp_path / "t.db"))
    parent = _insert(conn, "/lib/Été.JPG")
    sidecar = _insert(conn, "/lib/Été_1.JPG")
    cased_parent = _insert(conn, "/lib/b/IMG_0001.heic")
    cased_sidecar = _insert(conn, "/lib/b/IMG_0001_1.HEIC")
    conn.commit()
    assert detect_and_link_live_photo_sidecars(conn) == 2
    rows = dict(
        conn.execute(
            "SELECT id, live_photo_parent_id FROM photos WHERE is_live_photo_sidecar = 1"
        ).fetchall()
    )
    assert rows == {sidecar: parent, cased_sidecar: cased_parent}
