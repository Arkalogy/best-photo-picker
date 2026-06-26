"""``bpp model`` CLI subcommands — registry + acceptance from the
text shell.

Batch 4 / item 5 + trap T1 of the legal-posture rollout: a user
who never opens the GUI must hit the same click-through gate when
they pick a restricted model from a script or headless session.
``bpp model accept <id>`` is the CLI parity surface — it prints
the dialog text, prompts for each checkbox, and writes the
acceptance row through the same
:mod:`bpp.registry.acceptance` API the Flask endpoint uses.

Subcommands

* ``bpp model list`` — print the picker grouped by license posture.
* ``bpp model accept <id>`` — interactive click-through; the user
  answers four yes/no prompts, optionally provides a
  source-of-rights note, and the acceptance row is written.
* ``bpp model accepted`` — read-only listing of accepted-model
  rows.

The command's *not* a deep CLI — the GUI dialog is the primary
surface. It exists so a headless or automation context can still
reach a real acceptance row and so a release auditor can
demonstrate that the chokepoint is enforceable without the GUI.
"""

from __future__ import annotations

import argparse

from bpp.commands.model_commands import (
    do_byom_add,
    do_byom_list,
    do_byom_remove,
    do_model_accept,
    do_model_accepted,
    do_model_list,
    do_model_remove,
    do_registry_verify,
    do_use_context_set,
    do_use_context_show,
)
from bpp.registry import UseContext


def add_subparsers(model_sub: argparse._SubParsersAction) -> None:
    """Plug ``list`` / ``accept`` / ``accepted`` into the ``model``
    parser. Called from :mod:`bpp.cli`."""
    p_list = model_sub.add_parser("list", help="List registered models, grouped by license")
    p_list.set_defaults(_model_func=do_model_list)

    p_accept = model_sub.add_parser(
        "accept",
        help="Interactively accept a restricted-license model "
        "(text-mode parity with the GUI dialog)",
    )
    p_accept.add_argument("id", help="The model id to accept")
    p_accept.set_defaults(_model_func=do_model_accept)

    p_accepted = model_sub.add_parser(
        "accepted",
        help="Show acceptance log entries",
    )
    p_accepted.set_defaults(_model_func=do_model_accepted)

    p_uc = model_sub.add_parser(
        "use-context",
        help=(
            "Show or set the user's declared use context "
            "(commercial-use gate parity for headless invocations)"
        ),
    )
    uc_sub = p_uc.add_subparsers(dest="use_context_command")
    p_uc_show = uc_sub.add_parser("show", help="Print the current declaration + audit trail")
    p_uc_show.set_defaults(_model_func=do_use_context_show)
    p_uc_set = uc_sub.add_parser("set", help="Persist a new use-context declaration")
    p_uc_set.add_argument(
        "value",
        choices=[c.value for c in UseContext],
        help="One of: personal, research, commercial, unspecified",
    )
    p_uc_set.set_defaults(_model_func=do_use_context_set)

    p_byom = model_sub.add_parser(
        "byom",
        help=(
            "Bring-Your-Own-Model: register a local model file you "
            "supply (commercial escape hatch — Arkalogy is not in "
            "the rights chain)"
        ),
    )
    byom_sub = p_byom.add_subparsers(dest="byom_command")

    p_byom_list = byom_sub.add_parser("list", help="List registered BYOM entries")
    p_byom_list.set_defaults(_model_func=do_byom_list)

    p_byom_add = byom_sub.add_parser("add", help="Register a local model file")
    p_byom_add.add_argument(
        "--file",
        required=True,
        help="Path to the local model file (ONNX, etc.)",
    )
    p_byom_add.add_argument(
        "--display-name",
        default="",
        help=("Human-readable name shown in the picker. Defaults to the filename when empty."),
    )
    p_byom_add.add_argument(
        "--kind",
        default="face_embedder",
        help="Model kind. Default: face_embedder.",
    )
    p_byom_add.set_defaults(_model_func=do_byom_add)

    p_byom_remove = byom_sub.add_parser(
        "remove",
        help="Forget a BYOM entry (does not delete the file)",
    )
    p_byom_remove.add_argument("id", help="The BYOM entry id")
    p_byom_remove.set_defaults(_model_func=do_byom_remove)

    p_remove = model_sub.add_parser(
        "remove",
        help=(
            "Remove a model entry and optionally purge derived "
            "embeddings (item 21). Fails closed without an explicit "
            "--purge-derived or --keep-derived flag."
        ),
    )
    p_remove.add_argument("id", help="The model entry id (built-in or BYOM)")
    p_remove.add_argument(
        "--library",
        required=True,
        help=(
            "Path to the library directory whose face_embeddings "
            "table will be inspected (and possibly purged)"
        ),
    )
    p_remove.add_argument(
        "--purge-derived",
        action="store_true",
        help="Delete every face_embedding row produced by this model",
    )
    p_remove.add_argument(
        "--keep-derived",
        action="store_true",
        help=(
            "Keep face_embedding rows produced by this model. Removes the registry/BYOM entry only."
        ),
    )
    p_remove.set_defaults(_model_func=do_model_remove)

    p_registry = model_sub.add_parser(
        "registry",
        help=(
            "Remote-registry tooling: verify a local manifest file "
            "against the bundled trusted-key set"
        ),
    )
    registry_sub = p_registry.add_subparsers(dest="registry_command")
    p_reg_verify = registry_sub.add_parser(
        "verify",
        help=(
            "Verify a local registry manifest's Ed25519 signatures "
            "(dry-run of the startup-time check; exits 0 on success)"
        ),
    )
    p_reg_verify.add_argument("path", help="Path to a registry manifest JSON file")
    p_reg_verify.set_defaults(_model_func=do_registry_verify)
