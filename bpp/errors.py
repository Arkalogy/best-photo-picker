"""P7 — unified error hierarchy.

Pre-P7 the codebase had five disjoint exception classes — each a
direct ``Exception`` or ``ValueError`` subclass without a common
ancestor:

* ``ConfigValidationError(ValueError)``
* ``ServingLockError(Exception)``
* ``ModelIntegrityError(Exception)``
* ``FaceEmbeddingsTooLarge(RuntimeError)``
* ``ClipEmbeddingsTooLarge(RuntimeError)``

That made "catch every bpp-recoverable error" impossible without
enumerating each by name. The audit's structured-error story stalled
on the missing common base.

This module introduces :class:`BppError` as that base, plus five new
subclasses for new exception sites
(:class:`ValidationError`, :class:`NotFoundError`,
:class:`ConflictError`, :class:`ResourceExhaustedError`,
:class:`IntegrityError`). The five pre-existing exceptions are
virtual-registered via :meth:`BppError.register` — production code
can ``except BppError`` without breaking ``except ModelIntegrityError``
callers that already exist.

The base also carries a structured ``to_dict()`` representation so
Flask endpoints can return uniform error envelopes:

    try:
        ...
    except BppError as e:
        return jsonify(e.to_dict()), e.http_status

ADR: docs/adr/0004-error-hierarchy.md.
"""

from __future__ import annotations

import abc
from typing import Any, ClassVar


class _BppErrorMeta(abc.ABCMeta, type(Exception)):
    """Combine ``ABCMeta`` with Exception's metaclass.

    ABCMeta gives us :meth:`register` for virtual-subclass
    registration of the five legacy exceptions; Exception's metaclass
    (``type``) keeps ``raise BppError(...)`` working. Composing both
    keeps the metaclass MRO consistent without redefining the class
    twice.
    """


class BppError(Exception, metaclass=_BppErrorMeta):
    """Base class for all bpp-defined exceptions.

    Subclasses set class-level :attr:`http_status` and :attr:`code` to
    drive structured error responses. Defaults of 500 / "internal_error"
    apply when a subclass doesn't override.

    Virtual subclasses (pre-P7 exception classes) get registered via
    :meth:`BppError.register` so they ``isinstance(e, BppError)`` even
    though they don't actually subclass it. The class attributes
    above don't apply to virtual subclasses — they get the default
    500 / "internal_error" envelope unless the calling site overrides.
    """

    #: HTTP status code Flask should return when this exception bubbles
    #: out of an endpoint. Override per subclass to match the failure
    #: mode (400 for validation, 409 for conflict, 503 for resource
    #: exhausted, etc.). Default is 500 because an uncaught BppError
    #: signals an internal error.
    http_status: ClassVar[int] = 500

    #: Stable machine-readable identifier for the failure. UI / API
    #: clients can switch on this to render localized messages or
    #: trigger retry logic.
    code: ClassVar[str] = "internal_error"

    def __init__(
        self,
        message: str = "",
        *,
        user_message: str | None = None,
        diagnostic_message: str | None = None,
        **context: Any,
    ) -> None:
        """Build the exception.

        ``message`` is the human-readable description used as both the
        user-facing ``error`` field of the response AND the log line —
        appropriate for most call sites where the two coincide.

        For privacy-sensitive cases the two split:

        * ``user_message`` — what to show in the API response. Safe
          for the user to see. No filesystem paths, no internal IDs.
          Defaults to ``message`` when omitted.
        * ``diagnostic_message`` — what to log. May include
          filesystem paths, internal IDs, stack-trace hints. Stays
          out of the response envelope. Defaults to ``message`` when
          omitted.

        ``context`` — JSON-serializable kwargs surfaced as the
        ``context`` field of the response. Used for actionable
        details: offending photo id, missing config key, the count
        that exceeded the cap, etc. The context goes to the user;
        don't put filesystem paths or secrets here.
        """
        super().__init__(message)
        self._user_message = user_message if user_message is not None else message
        self._diagnostic_message = diagnostic_message if diagnostic_message is not None else message
        self.context = dict(context)

    @property
    def user_message(self) -> str:
        """The user-safe message rendered in the API response."""
        return self._user_message or self.code

    @property
    def diagnostic_message(self) -> str:
        """The detail-level message rendered in server.log.

        May contain filesystem paths or internal IDs. Never put this
        into ``to_dict()`` — the Flask error handler logs it
        separately from the response envelope.
        """
        return self._diagnostic_message or str(self)

    def to_dict(self) -> dict[str, Any]:
        """Return a structured response envelope.

        Shape::

            {
                "error": "<user_message>",
                "code": "<machine_readable_code>",
                "context": {<extra fields>},  # omitted when empty
            }

        Uses :attr:`user_message`, not :attr:`diagnostic_message` —
        responses must not leak diagnostic data. Subclasses can
        override to add or rename fields; the default covers the
        common case.
        """
        envelope: dict[str, Any] = {
            "error": self.user_message,
            "code": self.code,
        }
        if self.context:
            envelope["context"] = dict(self.context)
        return envelope


class ValidationError(BppError):
    """User input failed validation (400)."""

    http_status = 400
    code = "validation_error"


class NotFoundError(BppError):
    """Requested resource doesn't exist (404)."""

    http_status = 404
    code = "not_found"


class ForbiddenError(BppError):
    """Request is structurally valid but rejected by an authorization
    / capability check (403).

    Used for path-traversal blocks (the path resolves outside an
    allowed root), LAN-side mutating endpoints, and other "the caller
    is in the system but not allowed to do *this*" cases. Distinct
    from :class:`ValidationError` (input is malformed) and
    :class:`NotFoundError` (resource doesn't exist) — those carry
    different signals to the caller and different log severity.
    """

    http_status = 403
    code = "forbidden"


class ConflictError(BppError):
    """Operation conflicts with current state (409)."""

    http_status = 409
    code = "conflict"


class ResourceExhaustedError(BppError):
    """A bounded resource is full / over limit (503)."""

    http_status = 503
    code = "resource_exhausted"


class FeatureUnavailableError(BppError):
    """Optional feature is not installed / not enabled (501).

    Distinct from :class:`ResourceExhaustedError` (which means "the
    resource exists but is currently full / over limit"). This is the
    "you didn't install the optional dep" case — actionable via
    ``pip install bppicker[<extra>]``.
    """

    http_status = 501
    code = "feature_unavailable"


class IntegrityError(BppError):
    """Downstream data integrity check failed (500).

    Used for model file SHA mismatches, schema corruption, etc. —
    not a user error, but distinct from a generic internal_error
    because it usually means "redownload / restore-backup."
    """

    code = "integrity_error"


# Legacy pre-P7 exception classes
# (``ConfigValidationError``, ``ServingLockError``, ``ModelIntegrityError``,
# ``FaceEmbeddingsTooLarge``, ``ClipEmbeddingsTooLarge``) now inherit
# :class:`BppError` directly via multiple inheritance — see each
# legacy class for the back-compat parent it kept (``ValueError`` /
# ``RuntimeError`` / ``Exception``). ``except BppError`` catches them;
# pre-P7 ``except ValueError`` / ``except RuntimeError`` keep working.
#
# Earlier P7 drafts used :meth:`BppError.register` for virtual-subclass
# registration, but Python's ``except`` uses strict subtype matching
# and doesn't honour ABC's ``__subclasscheck__``. Real inheritance is
# the only way to make the legacy classes catchable as ``BppError``.
