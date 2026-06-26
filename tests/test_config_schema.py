"""R8-H12c: ConfigSchema validates Config.set() at write time.

Before: ``ctx.config.set("face_detection_confidence", -42)`` was
accepted and stored verbatim. Downstream readers got a negative
threshold, downstream code that called ``int("fifty")`` crashed
later — the bug was reported far from where the write happened.

After: every key with a registered schema is type-coerced + bound-
checked + enum-checked at write time, raising
``ConfigValidationError`` (subclass of ``ValueError``) on failure.
Unschematized keys pass through unchanged so plugin keys without
a schema, and free-form strings, keep working.
"""

from __future__ import annotations

from typing import Any

import pytest

from bpp.config_schema import (
    ConfigField,
    ConfigValidationError,
    get_schema,
    iter_schema,
    register_field,
    validate_value,
)


class TestValidateValueBasics:
    def test_unschematized_key_passes_through(self):
        # plugin / typo keys without schema return value unchanged
        assert validate_value("__no_such_key_in_schema__", "anything") == "anything"
        assert validate_value("__another__", 42) == 42

    def test_int_coerces_string_digits(self):
        assert validate_value("default_selection_k", "50") == 50

    def test_int_rejects_non_numeric_string(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_value("default_selection_k", "fifty")
        assert exc.value.key == "default_selection_k"

    def test_int_rejects_bool(self):
        # bool is a subclass of int in Python; the schema rejects
        # accidental bool→int coercion so toggles don't silently
        # become ints.
        with pytest.raises(ConfigValidationError):
            validate_value("default_selection_k", True)

    def test_float_coerces_int_and_string(self):
        assert validate_value("face_detection_confidence", 0.5) == 0.5
        assert validate_value("face_detection_confidence", "0.7") == 0.7

    def test_bool_coerces_truthy_strings(self):
        assert validate_value("follow_symlinks", "yes") is True
        assert validate_value("follow_symlinks", "no") is False
        assert validate_value("follow_symlinks", "true") is True
        assert validate_value("follow_symlinks", "0") is False

    def test_bool_rejects_garbage_string(self):
        with pytest.raises(ConfigValidationError):
            validate_value("follow_symlinks", "maybe")


class TestBoundsEnforcement:
    def test_below_min_rejected(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_value("face_detection_confidence", -0.5)
        assert "below minimum" in str(exc.value)

    def test_above_max_rejected(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_value("face_detection_confidence", 1.5)
        assert "above maximum" in str(exc.value)

    def test_at_boundary_allowed(self):
        """Min and max are inclusive."""
        assert validate_value("face_detection_confidence", 0.0) == 0.0
        assert validate_value("face_detection_confidence", 1.0) == 1.0

    def test_int_negative_rejected(self):
        # k must be ≥ 1
        with pytest.raises(ConfigValidationError):
            validate_value("default_selection_k", -5)

    def test_int_zero_rejected_when_min_is_one(self):
        with pytest.raises(ConfigValidationError):
            validate_value("default_selection_k", 0)


class TestChoicesEnforcement:
    def test_choices_passes_when_in_set(self):
        register_field(
            ConfigField(
                key="__test_choices__",
                type=str,
                choices=("low", "med", "high"),
            )
        )
        assert validate_value("__test_choices__", "med") == "med"

    def test_choices_rejects_when_outside_set(self):
        register_field(
            ConfigField(
                key="__test_choices2__",
                type=str,
                choices=("low", "med", "high"),
            )
        )
        with pytest.raises(ConfigValidationError) as exc:
            validate_value("__test_choices2__", "ultra")
        assert "not in allowed choices" in str(exc.value)


class TestCustomValidator:
    def test_validator_runs_after_coerce(self):
        captured: list[Any] = []

        def _validator(value: Any) -> None:
            captured.append(value)

        register_field(
            ConfigField(
                key="__test_validator__",
                type=int,
                validator=_validator,
            )
        )
        validate_value("__test_validator__", "42")
        assert captured == [42], "Validator must receive the post-coerce value"

    def test_validator_can_reject(self):
        def _validator(value: Any) -> None:
            if value % 2 != 0:
                raise ConfigValidationError("__test_validator2__", "must be even")

        register_field(
            ConfigField(
                key="__test_validator2__",
                type=int,
                validator=_validator,
            )
        )
        with pytest.raises(ConfigValidationError, match="must be even"):
            validate_value("__test_validator2__", 3)


class TestRegistryIntrospection:
    def test_existing_keys_have_schema(self):
        """Spot-check that the built-in registrations populate the
        registry. Doesn't enumerate every key — that would lock the
        list shape; we just want the registration mechanism to have
        run."""
        assert get_schema("face_detection_confidence") is not None
        assert get_schema("default_selection_k") is not None
        assert get_schema("follow_symlinks") is not None

    def test_iter_schema_yields_every_registered_field(self):
        keys = {f.key for f in iter_schema()}
        # Sanity floor — at least the four we know we register.
        assert "face_detection_confidence" in keys
        assert "default_selection_k" in keys
        assert "follow_symlinks" in keys
        assert "scan_extensions" in keys


class TestIntegrationWithConfigSet:
    """End-to-end through Config.set() — the whole point of the
    schema is that an out-of-bounds write fails immediately rather
    than persisting and corrupting a downstream read."""

    def test_config_set_rejects_out_of_bounds(self, tmp_path):
        from bpp.config_resolver import Config
        from bpp.db.connection import init_db

        db = init_db(str(tmp_path / "test.db"))
        cfg = Config(yaml_overlay={}, get_conn=lambda: db)

        with pytest.raises(ConfigValidationError):
            cfg.set("face_detection_confidence", 99.0)

        # And the DB doesn't have a stored value for that key
        assert cfg.get("face_detection_confidence") == pytest.approx(0.3)

    def test_config_set_rejects_wrong_type(self, tmp_path):
        from bpp.config_resolver import Config
        from bpp.db.connection import init_db

        db = init_db(str(tmp_path / "test.db"))
        cfg = Config(yaml_overlay={}, get_conn=lambda: db)

        with pytest.raises(ConfigValidationError):
            cfg.set("default_selection_k", "fifty")

    def test_config_set_unschematized_key_still_works(self, tmp_path):
        """A plugin key not yet in the schema must still persist —
        the registry is additive, not gatekeeping."""
        from bpp.config_resolver import Config
        from bpp.db.connection import init_db

        db = init_db(str(tmp_path / "test.db"))
        cfg = Config(yaml_overlay={}, get_conn=lambda: db)
        cfg.set("__plugin_only_key__", "anything")
        assert cfg.get("__plugin_only_key__") == "anything"


# ─── R9-extensibility-M1: load_config validates YAML keys ─────────────


class TestLoadConfigValidatesYAML:
    """Pre-fix, ``load_config(yaml_path)`` merged user-supplied keys
    over DEFAULTS without going through the schema. A YAML with
    ``face_detection_confidence: -42`` booted silently and passed the
    bad value to scorers. The fix routes each key through
    ``validate_value`` so YAML errors land at startup, not deep in a
    scoring loop."""

    def test_yaml_with_valid_keys_passes(self, tmp_path):
        from bpp.config import load_config

        yaml_path = tmp_path / "good.yaml"
        yaml_path.write_text(
            "face_detection_confidence: 0.7\ndefault_selection_k: 25\nmax_long_side: 1280\n"
        )

        cfg = load_config(str(yaml_path))
        assert cfg["face_detection_confidence"] == pytest.approx(0.7)
        assert cfg["default_selection_k"] == 25
        assert cfg["max_long_side"] == 1280

    def test_yaml_with_out_of_range_value_rejected(self, tmp_path):
        from bpp.config import load_config

        yaml_path = tmp_path / "bad_range.yaml"
        yaml_path.write_text("face_detection_confidence: -42\n")

        with pytest.raises(ConfigValidationError) as exc:
            load_config(str(yaml_path))
        assert exc.value.key == "face_detection_confidence"

    def test_yaml_with_wrong_type_rejected(self, tmp_path):
        from bpp.config import load_config

        yaml_path = tmp_path / "bad_type.yaml"
        yaml_path.write_text("default_selection_k: not-a-number\n")

        with pytest.raises(ConfigValidationError) as exc:
            load_config(str(yaml_path))
        assert exc.value.key == "default_selection_k"

    def test_yaml_with_unschematized_key_still_loads(self, tmp_path):
        """Plugin keys without a schema entry pass through — the
        registry is additive, same as Config.set()."""
        from bpp.config import load_config

        yaml_path = tmp_path / "plugin_key.yaml"
        yaml_path.write_text("__custom_plugin_key__: anything\n")

        cfg = load_config(str(yaml_path))
        assert cfg["__custom_plugin_key__"] == "anything"

    def test_yaml_string_int_coerced_at_load(self, tmp_path):
        """Schema-typed coercion happens at load time too — a YAML
        that quotes the int (e.g. via env-var templating) still
        ends up as the right type in the merged config."""
        from bpp.config import load_config

        yaml_path = tmp_path / "stringy.yaml"
        yaml_path.write_text('default_selection_k: "75"\n')

        cfg = load_config(str(yaml_path))
        assert cfg["default_selection_k"] == 75
        assert isinstance(cfg["default_selection_k"], int)
