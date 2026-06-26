"""TDD: album type string constants and get_affected_album_types() helper."""

from __future__ import annotations


class TestAlbumTypeConstants:
    def test_constants_exist(self):
        from bpp.db.smart_albums import (
            ALBUM_DUPLICATES,
            ALBUM_GROUP,
            ALBUM_NO_FACES,
            ALBUM_PERSON,
            ALBUM_PET,
            ALBUM_UNSORTED,
        )

        assert ALBUM_PERSON == "smart_person"
        assert ALBUM_PET == "smart_pet"
        assert ALBUM_UNSORTED == "smart_unsorted"
        assert ALBUM_GROUP == "smart_group"
        assert ALBUM_NO_FACES == "smart_no_faces"
        assert ALBUM_DUPLICATES == "smart_duplicates"

    def test_get_affected_album_types_face_cluster(self):
        from bpp.db.smart_albums import get_affected_album_types

        kinds = get_affected_album_types("face_cluster")
        assert "smart_person" in kinds
        assert "smart_unsorted" in kinds
        assert "smart_group" in kinds
        assert "smart_no_faces" in kinds

    def test_get_affected_album_types_pet(self):
        from bpp.db.smart_albums import get_affected_album_types

        kinds = get_affected_album_types("pet_detect")
        assert "smart_pet" in kinds

    def test_get_affected_album_types_unknown_returns_empty(self):
        from bpp.db.smart_albums import get_affected_album_types

        assert get_affected_album_types("unknown_domain") == ()

    def test_callers_use_constants_not_literals(self):
        """Source scan: key callers must import and use ALBUM_* constants."""
        from pathlib import Path

        files_to_check = [
            "bpp/web/bp_faces_manage.py",
            "bpp/web/face_worker.py",
            "bpp/web/import_worker.py",
            "bpp/web/bp_pets.py",
        ]

        for rel in files_to_check:
            src = Path(rel).read_text()
            assert '"smart_person"' not in src or "ALBUM_PERSON" in src, (
                f"{rel} still has hardcoded 'smart_person' literal"
            )
            assert '"smart_pet"' not in src or "ALBUM_PET" in src, (
                f"{rel} still has hardcoded 'smart_pet' literal"
            )


class TestRegisterAlbumDomain:
    """T3: open the domain → album_types mapping to plugins via
    ``register_album_domain``. A plugin that adds a new smart-album
    type should be able to opt into existing domain refreshes (or
    introduce its own domain key) without editing core code.
    """

    def setup_method(self):
        from bpp.db.smart_albums import _reset_album_domain_for_tests

        _reset_album_domain_for_tests()

    def teardown_method(self):
        from bpp.db.smart_albums import _reset_album_domain_for_tests

        _reset_album_domain_for_tests()

    def test_register_new_domain_key(self):
        from bpp.db.smart_albums import (
            get_affected_album_types,
            register_album_domain,
        )

        register_album_domain("my_plugin_event", ("smart_my_kind",))
        assert get_affected_album_types("my_plugin_event") == ("smart_my_kind",)

    def test_register_replaces_existing_domain_when_extend_false(self):
        from bpp.db.smart_albums import (
            get_affected_album_types,
            register_album_domain,
        )

        before = get_affected_album_types("face_cluster")
        assert "smart_person" in before  # built-in

        register_album_domain("face_cluster", ("smart_only_my_kind",))
        after = get_affected_album_types("face_cluster")
        # Default extend=False replaces the existing entry outright.
        assert after == ("smart_only_my_kind",)
        assert "smart_person" not in after

    def test_register_with_extend_appends_new_types(self):
        from bpp.db.smart_albums import (
            get_affected_album_types,
            register_album_domain,
        )

        before = get_affected_album_types("face_cluster")
        register_album_domain(
            "face_cluster",
            ("smart_my_kind",),
            extend=True,
        )
        after = get_affected_album_types("face_cluster")
        # All previous types still present.
        for t in before:
            assert t in after
        # New type appended.
        assert "smart_my_kind" in after

    def test_register_with_extend_dedupes(self):
        from bpp.db.smart_albums import (
            get_affected_album_types,
            register_album_domain,
        )

        before = get_affected_album_types("face_cluster")
        # Re-register with an existing built-in type — shouldn't appear twice.
        existing = before[0]
        register_album_domain("face_cluster", (existing,), extend=True)
        after = get_affected_album_types("face_cluster")
        assert after.count(existing) == 1

    def test_reset_for_tests_restores_builtins(self):
        from bpp.db.smart_albums import (
            _reset_album_domain_for_tests,
            get_affected_album_types,
            register_album_domain,
        )

        builtin_before = get_affected_album_types("face_cluster")
        register_album_domain("face_cluster", ("smart_only_my_kind",))
        assert get_affected_album_types("face_cluster") == ("smart_only_my_kind",)

        _reset_album_domain_for_tests()
        assert get_affected_album_types("face_cluster") == builtin_before
