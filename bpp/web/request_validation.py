"""Request validation decorators for blueprint endpoints.

56 endpoint sites currently start with the same
boilerplate:

    data = request.get_json(silent=True) or {}
    foo = data.get("foo")
    if not foo:
        return jsonify({"error": "foo required"}), 400
    bar = data.get("bar", "default")
    # ... actual handler logic ...

The repetition makes it hard to:
  - find every "required" / "default" rule (they're inline in
    each handler)
  - audit response shapes for consistency (some return
    `{"error": "..."}`, some `{"status": "error"}`, some bare
    text)
  - change the validation contract uniformly (e.g. add a
    machine-readable error code for a future API client)

The ``@validate_json`` decorator extracts the request body once
according to a schema and injects validated values as keyword
arguments to the handler. Endpoints that opt in get:

  - automatic 400 on missing required keys (uniform shape)
  - typed defaults for optional keys
  - one-line endpoint signatures: keys appear as named params

Adoption is incremental. Existing endpoints keep working until
their owner migrates them — there's no big-bang shape change in
this commit. The (future) response-envelope half of M6 lives in
the same module so callers can migrate both halves together.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import Response, jsonify, request

from bpp.utils.logging import get_logger

_log = get_logger(__name__)

# Sentinel for "no default — this key is required". Using a unique
# object instance instead of None so callers CAN supply None as a
# legitimate default (e.g. an optional album_id that defaults to
# None meaning "no album filter").
_REQUIRED = object()


@dataclass(frozen=True)
class JsonField:
    """One key in a `@validate_json` schema.

    ``required`` is True iff the caller didn't supply ``default``.
    Mark a key required by leaving the default sentinel; mark it
    optional by passing any explicit default (including None).
    """

    default: Any = _REQUIRED
    type: type | None = None  # None = no type coercion
    error_message: str = ""  # custom 400 message; default uses key name

    @property
    def required(self) -> bool:
        return self.default is _REQUIRED


def field(
    default: Any = _REQUIRED,
    *,
    type: type | None = None,
    error_message: str = "",
) -> JsonField:
    """Convenience constructor matching ``dataclasses.field`` shape.

    Use this in `@validate_json(...)` schemas:

        @validate_json(
            filepath=field(),                       # required
            confirmation=field(error_message="confirmation='delete' required"),
            limit=field(default=50, type=int),      # optional, coerced
        )
        def my_endpoint(filepath, confirmation, limit):
            ...
    """
    return JsonField(default=default, type=type, error_message=error_message)


def validate_json(**schema: JsonField) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: parse request JSON, validate against the schema,
    inject validated values as keyword arguments.

    Required keys missing from the body produce a 400 with a
    consistent ``{"error": "<key> required"}`` shape (overridable
    via the field's ``error_message``).

    Optional keys missing get the field's default. Type-typed
    fields run ``type(value)`` and 400 on TypeError / ValueError.

    Example:

        @bp.post("/api/v1/photos/delete")
        @validate_json(filepaths=field(), confirmation=field(default=""))
        def api_photos_delete(filepaths, confirmation):
            if confirmation != "delete":
                return jsonify({"error": "confirmation='delete' required"}), 400
            ...

    The decorator does NOT alter response shapes — that's the
    second half of M6 and lives separately so endpoints can
    migrate validation and response shape independently.
    """

    def decorator(view_func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(view_func)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True) or {}
            validated: dict[str, Any] = {}
            for key, fld in schema.items():
                if key in data:
                    raw = data[key]
                    if fld.type is not None:
                        try:
                            validated[key] = _coerce(key, raw, fld.type)
                        except (TypeError, ValueError):
                            return _bad_request(
                                fld.error_message
                                or f"{key} must be coercible to {fld.type.__name__}"
                            ), 400
                    else:
                        validated[key] = raw
                elif fld.required:
                    return _bad_request(fld.error_message or f"{key} required"), 400
                else:
                    validated[key] = fld.default
            # Inject validated values; let the handler take its own
            # ctx / id positional args alongside.
            kwargs.update(validated)
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def _coerce(key: str, raw: Any, target: type) -> Any:
    """Apply ``target(raw)`` with stricter container semantics.

    ``list(raw)`` on a string evaluates to a
    list of characters (``list("hello") == ['h','e','l','l','o']``)
    — silently lossy. Same hazard for ``dict(raw)`` on a non-mapping
    iterable that happens to yield 2-tuples. For container target
    types we refuse non-matching inputs up front rather than letting
    Python's permissive ctor produce surprising values.

    Scalar coercion (int, float, bool, str) keeps the previous
    permissive shape — ``int("42")`` and ``str(42)`` are documented
    `@validate_json(type=int|str)` behaviors.
    """
    if target is list and not isinstance(raw, list):
        raise TypeError(f"{key} must be a JSON array, got {type(raw).__name__}")
    if target is dict and not isinstance(raw, dict):
        raise TypeError(f"{key} must be a JSON object, got {type(raw).__name__}")
    if target is tuple and not isinstance(raw, (list, tuple)):
        raise TypeError(f"{key} must be a JSON array, got {type(raw).__name__}")
    return target(raw)


def _bad_request(message: str) -> Response:
    """Standard 400 response shape. Kept centralised so the (future)
    envelope migration changes one function instead of dozens of
    `jsonify({"error": ...})` callsites.

    emit a warning log so an operator
    debugging "user reported a 400 from the SPA" has a breadcrumb
    showing which route + which validation rule fired. Includes the
    request path but NOT the body — the body may contain sensitive
    payloads (filepaths, tokens) that don't belong in server.log.
    """
    try:
        path = request.path
    except RuntimeError:
        # No request context (unit tests calling _bad_request directly).
        path = "?"
    _log.warning("validate_json 400 on %s: %s", path, message)
    return jsonify({"error": message})
