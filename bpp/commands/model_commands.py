"""CLI command handlers for ``bpp model …`` — list / accept / accepted,
use-context show/set, BYOM list/add/remove, model remove, registry verify,
plus the interactive prompts.

Split out of bpp/commands/model.py for the 500-LOC cap; model.py keeps the
argparse wiring (``add_subparsers``) and imports these handlers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bpp.registry import (
    AcceptanceError,
    ModelRemovalError,
    UseContext,
    add_byom_entry,
    confirm_acceptance,
    count_derived_for_model,
    get_entry,
    iter_entries,
    list_acceptances,
    list_byom_entries,
    prepare_acceptance,
    read_record,
    remove_byom_entry,
    remove_model_with_derived_choice,
    set_use_context,
    utc_now_iso,
)
from bpp.registry.labels import group_for_entry


def _prompt_yes_no(prompt: str) -> bool:
    """Return ``True`` only when the user types an unambiguous yes."""
    response = input(prompt + " [y/N] ").strip().lower()
    return response in {"y", "yes"}


def _prompt_use_context() -> UseContext:
    """Walk the user through declaring their use context.

    Returns the parsed enum. The default on empty input is
    UNSPECIFIED so a user who hits enter without thinking does not
    accidentally claim commercial use.
    """
    print(
        "\nUse context (used to record what you declared at acceptance "
        "time):\n"
        "  1) personal — purely personal photo curation\n"
        "  2) research — academic / research use\n"
        "  3) commercial — paid work, client work, business, or "
        "commercial services\n"
        "  4) unspecified — skip (record as 'unspecified')\n"
    )
    while True:
        raw = input("Pick 1-4 [4]: ").strip() or "4"
        mapping = {
            "1": UseContext.PERSONAL,
            "2": UseContext.RESEARCH,
            "3": UseContext.COMMERCIAL,
            "4": UseContext.UNSPECIFIED,
        }
        if raw in mapping:
            return mapping[raw]
        print(f"  '{raw}' is not 1-4. Try again.")


def do_model_list(_args: argparse.Namespace) -> int:
    """Print every registered :class:`ModelEntry` grouped by
    license posture."""
    grouped: dict[tuple[str, str], list] = {}
    for entry in iter_entries():
        key = group_for_entry(entry)
        grouped.setdefault(key, []).append(entry)
    permissive_first = sorted(
        grouped.items(),
        key=lambda kv: 0 if "permissive" in kv[0][0].lower() else 1,
    )
    for (title, subtitle), entries in permissive_first:
        print(f"\n{title}")
        print(f"  {subtitle}")
        print()
        for e in entries:
            default_marker = " [default]" if e.default_for_kind else ""
            print(f"  - {e.id:<40} {e.display_name}{default_marker}\n    {e.license_summary}")
            if e.commercial_use_restriction_known:
                print("    commercial use: restricted per upstream license")
            print(f"    terms: {e.terms_url}")
    print()
    return 0


def do_model_accept(args: argparse.Namespace) -> int:
    """Walk the user through the click-through dialog from the
    text shell and persist the acceptance row.

    BYOM ids start with ``byom_`` and resolve through the BYOM
    store. Built-in / remote-registry ids resolve through the
    in-memory registry.
    """
    if args.id.startswith("byom_"):
        from bpp.registry import get_byom_entry

        byom = get_byom_entry(args.id)
        entry = byom.to_model_entry() if byom is not None else None
    else:
        entry = get_entry(args.id)
    if entry is None:
        print(
            f"bpp model accept: no entry with id={args.id!r}. Try "
            "`bpp model list` or `bpp model byom list`.",
            file=sys.stderr,
        )
        return 1
    if not entry.requires_explicit_ack:
        print(
            f"bpp model accept: model {entry.id!r} is permissively-"
            "licensed and does not require an explicit acceptance.",
        )
        return 0

    use_context = _prompt_use_context()
    draft = prepare_acceptance(entry, use_context=use_context)

    print()
    print("─" * 78)
    print(f" {draft.entry.display_name}")
    print("─" * 78)
    print()
    print(draft.compressed_disclaimer)
    print()
    print("Full terms:")
    print(draft.full_disclaimer)
    print()
    print("Commercial use is defined as:")
    print(f"  {draft.commercial_use_definition}")
    print()
    print(draft.biometric_responsibility_text)
    print()
    print("Required acknowledgments:")
    print()

    # Iterate the draft's required checkboxes — NOT the global
    # REQUIRED_ACK_CHECKBOXES constant. The draft carries the right
    # set for the dialog kind (canonical 4 vs BYOM 1), so the CLI
    # matches the GUI dispatch in bpp.registry.acceptance.
    responses: dict[str, bool] = {}
    for cb_id, text in draft.required_checkboxes:
        responses[cb_id] = _prompt_yes_no(f"  - {text}")

    if not all(responses.values()):
        print(
            "\nbpp model accept: you must acknowledge every required "
            "point to use this model. No acceptance recorded.",
            file=sys.stderr,
        )
        return 1

    separate_rights_asserted = False
    source_of_rights_note = ""
    if use_context is UseContext.COMMERCIAL:
        print()
        print(draft.separate_rights_assertion)
        separate_rights_asserted = _prompt_yes_no(
            "Are you asserting separate commercial rights for this model?"
        )
        if not separate_rights_asserted:
            print(
                "\nbpp model accept: commercial use without separate "
                "rights is not permitted by upstream terms. No "
                "acceptance recorded.",
                file=sys.stderr,
            )
            return 1
        note = input(
            "  (optional) Note where your rights come from — stored locally for your records:\n  "
        ).strip()
        source_of_rights_note = note

    try:
        row = confirm_acceptance(
            draft,
            checkbox_responses=responses,
            accepted_at=utc_now_iso(),
            separate_rights_asserted=separate_rights_asserted,
            source_of_rights_note=source_of_rights_note,
        )
    except AcceptanceError as exc:
        print(f"\nbpp model accept: {exc}", file=sys.stderr)
        return 1
    print(
        f"\nAcceptance recorded for {row.model_id} at {row.accepted_at}.\n"
        f"  Use context: {row.use_context_at_acceptance}\n"
        f"  Separate rights asserted: {row.separate_rights_asserted}",
    )
    return 0


def do_model_accepted(_args: argparse.Namespace) -> int:
    """Read-only list of the user's acceptances."""
    rows = list_acceptances()
    if not rows:
        print("No acceptances recorded. Run `bpp model accept <id>` to create one.")
        return 0
    for row in rows:
        print(
            f"{row.accepted_at}  {row.model_id}\n"
            f"    use_context={row.use_context_at_acceptance}, "
            f"separate_rights_asserted={row.separate_rights_asserted}, "
            f"ack_text_version={row.ack_text_version}"
        )
    return 0


def do_use_context_show(_args: argparse.Namespace) -> int:
    """Print the user's current declared use context + audit
    trail."""
    record = read_record()
    print(f"Current use context: {record.use_context.value}")
    if record.set_at:
        print(f"  Set at: {record.set_at}")
        print(f"  Set via: {record.set_via}")
    if record.history:
        print(f"  Prior declarations ({len(record.history)}):")
        for prior in record.history:
            print(
                f"    - {prior.get('use_context', '?')} "
                f"({prior.get('set_at', '?')}, via "
                f"{prior.get('set_via', '?')})"
            )
    return 0


def do_use_context_set(args: argparse.Namespace) -> int:
    """Persist a use-context declaration via the CLI.

    Batch 5 / item 15: the gate the user sees on first launch is
    the GUI / web flow. The CLI parity exists so headless
    invocations can declare the same context without spinning the
    web UI; the CLI label is recorded as the ``set_via`` field.
    """
    try:
        ctx = UseContext(args.value)
    except ValueError:
        print(
            f"bpp model use-context set: unknown value {args.value!r}. "
            f"Expected one of: "
            f"{', '.join(c.value for c in UseContext)}.",
            file=sys.stderr,
        )
        return 1
    record = set_use_context(ctx, set_via="cli")
    print(f"Use context now: {record.use_context.value} (set via cli)")
    return 0


def do_byom_list(_args: argparse.Namespace) -> int:
    """List the user's registered Bring-Your-Own-Model entries."""
    entries = list_byom_entries()
    if not entries:
        print(
            "No BYOM entries registered. Use `bpp model byom add "
            "--file <path>` to register a model file."
        )
        return 0
    for e in entries:
        print(
            f"{e.id}\n"
            f"  display_name: {e.display_name}\n"
            f"  kind: {e.kind}\n"
            f"  file_path: {e.file_path}\n"
            f"  weight_sha256: {e.weight_sha256[:16]}…\n"
            f"  added_at: {e.added_at}"
        )
    return 0


def do_byom_add(args: argparse.Namespace) -> int:
    """Register a user-supplied model file.

    The CLI does NOT walk the user through the BYOM acceptance
    dialog here — the acceptance flow is the same one that runs
    for any restricted entry, available via ``bpp model accept
    <id>`` after registration. Two-step on purpose: ``add``
    establishes the file path + SHA-256, ``accept`` records the
    user-responsibility acknowledgment.
    """
    try:
        entry = add_byom_entry(
            display_name=args.display_name,
            kind=args.kind,
            file_path=Path(args.file).expanduser(),
        )
    except FileNotFoundError as exc:
        print(f"bpp model byom add: {exc}", file=sys.stderr)
        return 1
    print(
        f"Registered BYOM entry: {entry.id}\n"
        f"  Run `bpp model accept {entry.id}` to record the "
        "user-responsibility acknowledgment before BPP can use it."
    )
    return 0


def do_byom_remove(args: argparse.Namespace) -> int:
    """Forget a BYOM entry. Does NOT delete the user's file."""
    if remove_byom_entry(args.id):
        print(f"Removed BYOM entry: {args.id}. The model file on disk was not deleted.")
        return 0
    print(
        f"bpp model byom remove: no entry with id {args.id!r}.",
        file=sys.stderr,
    )
    return 1


def do_model_remove(args: argparse.Namespace) -> int:
    """Remove a model with explicit derived-data choice.

    Batch 7 / item 21 + Q8: the CLI fails closed when neither
    ``--purge-derived`` nor ``--keep-derived`` is supplied. The
    user must say what to do with the embeddings produced by the
    model — silent removal that left the biometric data behind
    would undercut the privacy posture the legal-posture spec wanted.
    """
    if args.purge_derived == args.keep_derived:
        print(
            "bpp model remove: specify exactly one of "
            "--purge-derived or --keep-derived. The CLI fails "
            "closed because a silent default that leaves biometric "
            "data behind would undercut the privacy posture.",
            file=sys.stderr,
        )
        return 1

    # Look up the library DB to count + purge derived rows.
    # ``--library`` is a directory path; the actual SQLite file lives
    # at ``<library>/data/photopicker.db`` (matches the layout the
    # serve / pick / demo commands use).
    import os

    from bpp.db.connection import get_db
    from bpp.db.library import get_library_dirs

    try:
        db_path = os.path.join(get_library_dirs(args.library)["data"], "photopicker.db")
    except Exception as exc:
        print(
            f"bpp model remove: cannot resolve library DB path under {args.library!r}: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        with get_db(db_path) as conn:
            summary = count_derived_for_model(args.id, conn)
            print(
                f"Removing {args.id} will affect:\n"
                f"  face_embeddings rows: {summary.embeddings}\n"
                f"  distinct clusters:    {summary.distinct_clusters}\n"
                f"  distinct photos:      {summary.distinct_photos}"
            )
            try:
                result = remove_model_with_derived_choice(
                    args.id,
                    purge_derived=args.purge_derived,
                    conn=conn,
                )
            except ModelRemovalError as exc:
                print(
                    f"bpp model remove: {exc}",
                    file=sys.stderr,
                )
                conn.rollback()
                return 1
            conn.commit()
    except Exception as exc:
        print(
            f"bpp model remove: failed to open library DB at {args.library!r}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"Removed entry: {result.model_id} (kind={result.entry_kind}). "
        f"Derived data purged: {result.purged}."
    )
    return 0


def do_registry_verify(args: argparse.Namespace) -> int:
    """Verify a local remote-registry manifest file against the bundled
    trusted-key set.

    Batch 8 / item 23: gives a release auditor and a registry signer a
    way to dry-run the verifier without needing the BPP server, the GUI,
    or a network fetch. Exits ``0`` on success, ``1`` on any verification
    failure (bad signatures, malformed JSON, missing fields, unknown
    keys). The errors are printed verbatim so the signer can fix and
    re-sign.
    """
    import json

    from bpp.registry import trusted_key_list, verify_manifest

    path = Path(args.path).expanduser()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        print(
            f"bpp model registry verify: cannot read {path}: {exc}",
            file=sys.stderr,
        )
        return 1
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(
            f"bpp model registry verify: {path} is not valid JSON: {exc}",
            file=sys.stderr,
        )
        return 1

    result = verify_manifest(manifest, trusted_keys=trusted_key_list())
    if result.is_valid:
        print(
            f"OK: manifest at {path} verifies against "
            f"{len(result.valid_signatures)} bundled key(s): "
            f"{', '.join(sorted(result.valid_signatures))}\n"
            f"  entries: {len(result.entries)}"
        )
        return 0
    print(
        f"FAIL: manifest at {path} did NOT verify.",
        file=sys.stderr,
    )
    for err in result.errors:
        print(f"  - {err}", file=sys.stderr)
    return 1
