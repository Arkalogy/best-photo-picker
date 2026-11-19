"""Indexes and the app's own triggers, run on every library open.

Split out of bpp.db.schema (500-LOC cap) and re-exported from there.
The triggers defined here are the only ones a library database may
carry: see :mod:`bpp.db.schema_guard`.
"""

from __future__ import annotations

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_photos_filepath ON photos(filepath);
CREATE INDEX IF NOT EXISTS idx_photos_sha256 ON photos(sha256);
CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(date);
CREATE INDEX IF NOT EXISTS idx_photos_missing ON photos(missing);
CREATE INDEX IF NOT EXISTS idx_album_photos_album ON album_photos(album_id);
CREATE INDEX IF NOT EXISTS idx_album_photos_photo ON album_photos(photo_id);
CREATE INDEX IF NOT EXISTS idx_face_embeddings_photo ON face_embeddings(photo_id);
CREATE INDEX IF NOT EXISTS idx_face_embeddings_cluster ON face_embeddings(cluster_id);
CREATE INDEX IF NOT EXISTS idx_face_embeddings_cluster_photo
    ON face_embeddings(cluster_id, photo_id);
-- v41: covering index for the derived-data-purge filter.
CREATE INDEX IF NOT EXISTS idx_face_embeddings_producing_model
    ON face_embeddings(producing_model_id);
CREATE INDEX IF NOT EXISTS idx_clip_embeddings_photo ON clip_embeddings(photo_id);
CREATE INDEX IF NOT EXISTS idx_dedup_feedback_photos ON dedup_feedback(photo_id_a, photo_id_b);
CREATE INDEX IF NOT EXISTS idx_dedup_feedback_album ON dedup_feedback(album_id);
CREATE INDEX IF NOT EXISTS idx_photos_deleted ON photos(deleted_at);
CREATE INDEX IF NOT EXISTS idx_photos_has_cat ON photos(has_cat);
CREATE INDEX IF NOT EXISTS idx_photos_has_dog ON photos(has_dog);
CREATE INDEX IF NOT EXISTS idx_pet_detections_photo ON pet_detections(photo_id);
CREATE INDEX IF NOT EXISTS idx_pet_detections_cluster ON pet_detections(cluster_id);
CREATE INDEX IF NOT EXISTS idx_photo_person_tags_photo ON photo_person_tags(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_person_tags_cluster ON photo_person_tags(cluster_id);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
CREATE INDEX IF NOT EXISTS idx_photo_tags_photo ON photo_tags(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_albums_parent ON albums(parent_id);
CREATE INDEX IF NOT EXISTS idx_photos_hidden ON photos(hidden_at);
CREATE INDEX IF NOT EXISTS idx_photos_date_month ON photos(date_month);
CREATE INDEX IF NOT EXISTS idx_photo_edits_photo ON photo_edits(photo_id);
CREATE INDEX IF NOT EXISTS idx_photos_aggregate_score ON photos(aggregate_score);
CREATE INDEX IF NOT EXISTS idx_photos_phash ON photos(phash);
CREATE INDEX IF NOT EXISTS idx_photos_is_video ON photos(is_video);
CREATE INDEX IF NOT EXISTS idx_album_photos_album_selected ON album_photos(album_id, selected);
CREATE UNIQUE INDEX IF NOT EXISTS idx_albums_type_rule ON albums(album_type, rule_json); -- v44
-- v36: partial index on the shadow column. Only rows where the column
-- is non-NULL get indexed (smart_person albums); everything else is
-- ignored. Lookup pattern is "find the album for cluster X" which is
-- now an O(log N) probe instead of a full-table scan + JSON parse.
CREATE INDEX IF NOT EXISTS idx_albums_smart_person_cluster
    ON albums(smart_person_cluster_id)
    WHERE smart_person_cluster_id IS NOT NULL;

-- v38: auto-populate the smart_person_cluster_id shadow column from
-- the cluster_id field of rule_json on every INSERT/UPDATE of a
-- smart_person album. Single source of truth — writers can't "forget"
-- the shadow column. The trigger uses json_extract (sqlite3 has it
-- built in since 3.38); falls back to LIKE-style extraction would be
-- an option for older sqlite but every supported python ships 3.38+.
CREATE TRIGGER IF NOT EXISTS trg_albums_sync_smart_person_cluster_insert
AFTER INSERT ON albums
WHEN NEW.album_type = 'smart_person' AND NEW.rule_json IS NOT NULL
BEGIN
    UPDATE albums
    SET smart_person_cluster_id = CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER)
    WHERE id = NEW.id
      AND json_extract(NEW.rule_json, '$.cluster_id') IS NOT NULL
      AND (smart_person_cluster_id IS NULL
           OR smart_person_cluster_id !=
              CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER));
END;

CREATE TRIGGER IF NOT EXISTS trg_albums_sync_smart_person_cluster_update
AFTER UPDATE OF rule_json, album_type ON albums
WHEN NEW.album_type = 'smart_person' AND NEW.rule_json IS NOT NULL
BEGIN
    UPDATE albums
    SET smart_person_cluster_id = CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER)
    WHERE id = NEW.id
      AND json_extract(NEW.rule_json, '$.cluster_id') IS NOT NULL
      AND (smart_person_cluster_id IS NULL
           OR smart_person_cluster_id !=
              CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER));
END;
CREATE INDEX IF NOT EXISTS idx_share_access_log_ts ON share_access_log(ts);
CREATE INDEX IF NOT EXISTS idx_share_devices_fingerprint ON share_devices(fingerprint);
CREATE INDEX IF NOT EXISTS idx_share_devices_state ON share_devices(trusted_at, revoked_at);
CREATE INDEX IF NOT EXISTS idx_operation_journal_pending
    ON operation_journal(kind, completed_at);
CREATE INDEX IF NOT EXISTS idx_face_extraction_journal_pending
    ON face_extraction_journal(completed_at);
-- partial index on the GPS columns. Sparse data (5-30% of
-- photos have GPS), so a partial index keeps the b-tree compact
-- AND lets `WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL`
-- queries skip most of the table without scanning.
CREATE INDEX IF NOT EXISTS idx_photos_gps ON photos(gps_lat, gps_lon)
    WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL;
-- partial index covering the Moments query (moment_size > 1) —
-- mirrors the v42 migration so fresh DBs match migrated ones.
CREATE INDEX IF NOT EXISTS idx_photos_moment ON photos (moment_cluster_id)
    WHERE moment_size > 1;
-- partial index covering the Sensitive smart-album scan: flagged photos
-- are sparse (model hits + manual overrides), so index only those rows.
-- Mirrors the v43 migration.
CREATE INDEX IF NOT EXISTS idx_photos_sensitive ON photos (nudity_score)
    WHERE nudity_score > 0 OR sensitive_override IS NOT NULL;
"""
