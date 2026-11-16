"""A request field that means on/off must be a JSON true or false.

bool() reads any non-empty string as true, so "false", "off" or a
checkbox's "on" would switch a flag ON. These tests pin the shared
helper and the routes that used bool() on request data.
"""

from __future__ import annotations

import os

import pytest

NOT_BOOLEANS = ["on", "off", "false", "true", "", 1, 0, None, [], {}]


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


class TestJsonBool:
    @pytest.mark.parametrize("value", [True, False])
    def test_returns_a_real_boolean_unchanged(self, value):
        from bpp.web.request_validation import json_bool

        assert json_bool({"flag": value}, "flag") is value

    @pytest.mark.parametrize("value", NOT_BOOLEANS)
    def test_refuses_anything_else(self, value):
        from bpp.errors import ValidationError
        from bpp.web.request_validation import json_bool

        with pytest.raises(ValidationError, match="must be true or false"):
            json_bool({"flag": value}, "flag", default=False)

    def test_missing_key_without_default_is_refused(self):
        from bpp.errors import ValidationError
        from bpp.web.request_validation import json_bool

        with pytest.raises(ValidationError, match="missing 'flag'"):
            json_bool({}, "flag")

    @pytest.mark.parametrize("default", [True, False])
    def test_missing_key_uses_the_default(self, default):
        from bpp.web.request_validation import json_bool

        assert json_bool({}, "flag", default=default) is default


class TestValidateJsonBoolField:
    @pytest.mark.parametrize("value", NOT_BOOLEANS)
    def test_decorator_bool_field_refuses_non_booleans(self, value):
        from bpp.web.request_validation import _coerce

        with pytest.raises(TypeError, match="must be true or false"):
            _coerce("flag", value, bool)

    @pytest.mark.parametrize("value", [True, False])
    def test_decorator_bool_field_accepts_booleans(self, value):
        from bpp.web.request_validation import _coerce

        assert _coerce("flag", value, bool) is value


class TestRoutesRefuseNonBooleans:
    @pytest.mark.parametrize("value", ["false", "off", 0, None])
    def test_clip_cap_override_is_not_switched_on(self, app, value):
        from bpp.db.clip import CLIP_MAX_OVERRIDE_KEY
        from bpp.db.settings import get_setting

        r = app.test_client().post("/api/v1/settings/clip_max_override", json={"enable": value})

        assert r.status_code == 400, f"enable={value!r} was accepted: {r.get_json()}"
        ctx = app.extensions["bpp"]
        assert get_setting(ctx.get_conn(), CLIP_MAX_OVERRIDE_KEY) is None, (
            f"enable={value!r} wrote the override"
        )

    @pytest.mark.parametrize("value", ["false", "no", 0, None])
    def test_model_removal_purge_choice_must_be_a_boolean(self, app, value):
        r = app.test_client().post(
            "/api/v1/model-registry/removal",
            json={"model_id": "sface_yunet", "purge_derived": value},
        )

        assert r.status_code == 400
        assert "must be true or false" in str(r.get_json()), r.get_json()

    @pytest.mark.parametrize("value", ["false", "off", 0, None])
    def test_import_sidecar_option_must_be_a_boolean(self, app, tmp_path, value):
        source = tmp_path / "source"
        source.mkdir()

        r = app.test_client().post(
            "/api/v1/import",
            json={"source_dir": str(source), "import_live_photo_sidecars": value},
        )

        assert r.status_code == 400
        assert "must be true or false" in str(r.get_json()), r.get_json()

    def test_clip_cap_override_needs_the_field(self, app):
        """An empty or misspelled body must not clear the override."""
        from bpp.db.clip import CLIP_MAX_OVERRIDE_KEY
        from bpp.db.settings import get_setting

        client = app.test_client()
        assert (
            client.post("/api/v1/settings/clip_max_override", json={"enable": True}).status_code
            == 200
        )

        r = client.post("/api/v1/settings/clip_max_override", json={"enabled": False})

        assert r.status_code == 400
        ctx = app.extensions["bpp"]
        assert get_setting(ctx.get_conn(), CLIP_MAX_OVERRIDE_KEY) is not None, (
            "a body without 'enable' cleared it"
        )

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("post", "/api/v1/batch/favorite", {"filepaths": ["/x/a.jpg"], "favorite": "false"}),
            (
                "post",
                "/api/v1/albums/1/batch/favorite",
                {"filepaths": ["/x/a.jpg"], "favorite": "false"},
            ),
            ("post", "/api/v1/faces/restore", {"all": "false"}),
            ("delete", "/api/v1/faces/purge", {"all": "false"}),
            (
                "put",
                "/api/v1/libraries/rename",
                {"path": "/x/lib", "name": "New", "rename_folder": "false"},
            ),
            ("post", "/api/v1/analyze", {"input_dir": "/x/photos", "recursive": "false"}),
        ],
    )
    def test_other_on_off_fields_must_be_booleans(self, app, method, path, body):
        r = getattr(app.test_client(), method)(path, json=body)

        assert r.status_code == 400, f"{method.upper()} {path} accepted {body}: {r.get_json()}"
        assert "must be true or false" in str(r.get_json()), r.get_json()

    def test_validate_json_reports_a_bool_field_plainly(self, app):
        from bpp.web.request_validation import field, validate_json

        @app.post("/__test_bool_field")
        @validate_json(flag=field(type=bool))
        def _view(flag):  # pragma: no cover - never reached with a bad value
            return {"flag": flag}

        r = app.test_client().post("/__test_bool_field", json={"flag": "off"})

        assert r.status_code == 400
        assert "must be true or false" in str(r.get_json()), r.get_json()
