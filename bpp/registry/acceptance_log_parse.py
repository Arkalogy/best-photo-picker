"""Reading the acceptance log: per-line parsing and the fail-closed rule.

Split from :mod:`bpp.registry.acceptance_log` (500-LOC cap). A damaged
line (bad bytes, bad JSON, wrong shape, a field of the wrong type) is
skipped whole — never loaded with altered text, since this is the
acceptance record — and can't sink the rest. Because a damaged line may
have been the user's withdrawal, an acceptance followed by one reads as
withdrawn until the user accepts again.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from bpp.registry.acceptance_log import AcceptanceRow

_log = get_logger(__name__)

#: Required row fields that must be text.
ROW_TEXT_FIELDS = (
    "model_id",
    "model_sha256",
    "ack_text_version",
    "ack_text_sha256",
    "use_context_text_version",
    "use_context_text_sha256",
    "use_context_at_acceptance",
    "terms_url",
    "terms_retrieved_at",
    "accepted_at",
)

#: A complete ``"model_id": "..."`` pair in a line too damaged to parse
#: (e.g. torn by a power cut after the id was written).
_MODEL_ID_RE = re.compile(rb'"model_id"\s*:\s*"([A-Za-z0-9_.\-]{1,200})"')


@dataclasses.dataclass(frozen=True)
class Parsed:
    rows: tuple[AcceptanceRow, ...]
    #: ``(rows parsed before it, model_id it names or None)`` per damaged
    #: line. A damaged line could have been a withdrawal, so an earlier
    #: acceptance must not count again (see :func:`fail_closed`).
    damaged: tuple[tuple[int, str | None], ...]


def _damaged_model_id(raw: object, line: bytes) -> str | None:
    if isinstance(raw, dict):
        return raw["model_id"] if isinstance(raw.get("model_id"), str) else None
    # A released build could glue the next row onto a torn one; then an
    # id found on the line may be the glued row's, not the torn row's.
    # Trust a recovered id only when it's the only one and nothing is
    # glued on (otherwise withdrawing every earlier acceptance is safe).
    ids = _MODEL_ID_RE.findall(line)
    if len(ids) != 1 or _has_glued_object(line):
        return None
    return ids[0].decode("ascii")


def _has_glued_object(line: bytes) -> bool:
    """True if some ``{"`` after the start begins a JSON object that runs
    to the end of the line — a complete row glued onto a torn one."""
    for match in re.finditer(rb'\{"', line):
        if match.start() == 0:
            continue
        try:
            if isinstance(json.loads(line[match.start() :]), dict):
                return True
        except ValueError:
            continue
    return False


def parse(target: Path) -> Parsed:
    from bpp.registry.acceptance_log import AcceptanceRow
    from bpp.utils.json_utils import safe_json_loads

    rows: list[AcceptanceRow] = []
    damaged: list[tuple[int, str | None]] = []
    with target.open("rb") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            raw = safe_json_loads(
                line, None, expected_type=dict, context=f"acceptance log line {line_no}"
            )
            try:
                if raw is None:
                    raise ValueError("not a JSON object")
                rows.append(AcceptanceRow.from_dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                damaged.append((len(rows), _damaged_model_id(raw, line)))
                _log.warning(
                    "Skipping unparseable acceptance-log line %d (%s); file=%s",
                    line_no,
                    exc,
                    target,
                )
    parsed = Parsed(rows=tuple(rows), damaged=tuple(damaged))
    # Once per file version (the caller caches by size + mtime), not on
    # every license check.
    for model_id in withdrawn_by_damage(parsed):
        _log.warning(
            "A damaged acceptance-log line follows the acceptance of %s; "
            "treating it as withdrawn until it's accepted again. file=%s",
            model_id,
            target,
        )
    return parsed


def fail_closed(parsed: Parsed, index: int, row: AcceptanceRow) -> AcceptanceRow:
    """*row* (the latest for its model, at *index*), read as a withdrawal
    if it's an acceptance followed by a damaged line that could concern
    the model. Fail closed — ask again, never treat it as still accepted
    (max legal protection)."""
    if row.event == "accept" and any(
        after > index and damaged_id in (None, row.model_id) for after, damaged_id in parsed.damaged
    ):
        return dataclasses.replace(row, event="revoke")
    return row


def latest_rows(parsed: Parsed) -> dict[str, tuple[int, AcceptanceRow]]:
    """Each model's last readable row and its index."""
    latest: dict[str, tuple[int, AcceptanceRow]] = {}
    for i, row in enumerate(parsed.rows):
        latest[row.model_id] = (i, row)
    return latest


def withdrawn_by_damage(parsed: Parsed) -> list[str]:
    """Models whose latest acceptance reads as withdrawn only because a
    damaged line follows it."""
    return [
        mid
        for mid, (i, row) in latest_rows(parsed).items()
        if row.event == "accept" and fail_closed(parsed, i, row).event == "revoke"
    ]
