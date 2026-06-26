"""CLI entrypoint for bpp."""

from __future__ import annotations

import argparse
import sys

from bpp import APP_NAME, __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bpp",
        description=f"{APP_NAME} — score, deduplicate, and select the best photos.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for determinism")
    # default pulled from the config registry so a plugin
    # override (e.g. AVIF / RAW format support) propagates without
    # touching this argparse declaration.
    from bpp.config import DEFAULTS

    parser.add_argument(
        "--extensions",
        default=DEFAULTS["scan_extensions"],
        help="Comma-separated image extensions to include (default from config: scan_extensions)",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # --- analyze ---
    p_analyze = sub.add_parser("analyze", help="Scan images, extract features, compute scores")
    p_analyze.add_argument("--input", required=True, help="Input folder of photos")
    p_analyze.add_argument("--out", required=True, help="Working directory for cache/results")
    p_analyze.add_argument("--config", help="Path to YAML config file")
    p_analyze.add_argument("--max", type=int, default=0, help="Max images to process (0=all)")
    p_analyze.add_argument("--workers", type=int, default=0, help="Parallel workers (0=auto)")
    p_analyze.add_argument("--debug", action="store_true", help="Enable debug logging")
    p_analyze.add_argument("--dry-run", action="store_true", help="Show what would be done")

    # --- select ---
    p_select = sub.add_parser("select", help="Select best photos from analyzed data")
    p_select.add_argument("--workdir", required=True, help="Working directory from analyze step")
    p_select.add_argument("--k", type=int, default=50, help="Number of photos to select")
    p_select.add_argument("--out", required=True, help="Output directory for selected photos")
    p_select.add_argument("--config", help="Path to YAML config file")
    p_select.add_argument("--gallery", action="store_true", help="Generate HTML gallery")
    p_select.add_argument("--dry-run", action="store_true", help="Show what would be done")
    mode = p_select.add_mutually_exclusive_group()
    mode.add_argument("--copy", action="store_const", const="copy", dest="export_mode")
    mode.add_argument("--hardlink", action="store_const", const="hardlink", dest="export_mode")
    mode.add_argument("--symlink", action="store_const", const="symlink", dest="export_mode")
    p_select.set_defaults(export_mode="copy")

    # --- run (one-shot) ---
    p_run = sub.add_parser("run", help="Analyze + select in one shot")
    p_run.add_argument("--input", required=True, help="Input folder of photos")
    p_run.add_argument("--k", type=int, default=50, help="Number of photos to select")
    p_run.add_argument("--out", required=True, help="Output directory for selected photos")
    p_run.add_argument("--config", help="Path to YAML config file")
    p_run.add_argument("--max", type=int, default=0, help="Max images to process (0=all)")
    p_run.add_argument("--workers", type=int, default=0, help="Parallel workers (0=auto)")
    p_run.add_argument("--gallery", action="store_true", help="Generate HTML gallery")
    p_run.add_argument("--debug", action="store_true", help="Enable debug logging")
    p_run.add_argument("--dry-run", action="store_true", help="Show what would be done")
    run_mode = p_run.add_mutually_exclusive_group()
    run_mode.add_argument("--copy", action="store_const", const="copy", dest="export_mode")
    run_mode.add_argument("--hardlink", action="store_const", const="hardlink", dest="export_mode")
    run_mode.add_argument("--symlink", action="store_const", const="symlink", dest="export_mode")
    p_run.set_defaults(export_mode="copy")

    # --- web ---
    p_web = sub.add_parser("web", help="Launch interactive web UI")
    p_web.add_argument("--input", help="Input folder of photos")
    p_web.add_argument("--workdir", help="Working directory with existing analysis")
    p_web.add_argument("--port", type=int, default=5001, help="Port for web server")
    p_web.add_argument(
        "--host",
        default=None,
        help="Bind address (default: 127.0.0.1). Pass 0.0.0.0 for Docker port-forwarding.",
    )
    p_web.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    p_web.add_argument("--config", help="Path to YAML config file")
    p_web.add_argument("--debug", action="store_true", help="Enable debug logging")

    # --- serve ---
    p_serve = sub.add_parser("serve", help="Start photo management server")
    p_serve.add_argument(
        "--library",
        help="Library path (default: registry's active library, or ~/Pictures/BestPhotoPicker)",
    )
    p_serve.add_argument(
        "--host",
        default=None,
        help="Bind address. Default: 127.0.0.1 (loopback only) when LAN "
        "sharing is OFF, 0.0.0.0 when it's ON. Pass `0.0.0.0` explicitly "
        "to bind every interface regardless of the share toggle. The "
        "share toggle (Settings → Share) is what gates LAN access in "
        "the auth layer; binding loopback-only is defense-in-depth so "
        "the service isn't even visible on port scan from a coffee-shop "
        "Wi-Fi network. If running behind a reverse proxy (nginx, "
        "Caddy, Docker), set BPP_TRUSTED_PROXIES to the proxy's CIDR "
        "so X-Forwarded-For is honored for the loopback gate — see "
        "docs/security.md.",
    )
    p_serve.add_argument("--port", type=int, default=5001, help="Port for web server")
    p_serve.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    p_serve.add_argument("--config", help="Path to YAML config file")
    p_serve.add_argument("--debug", action="store_true", help="Enable debug logging")

    # --- demo ---
    p_demo = sub.add_parser("demo", help="Launch demo with sample photos")
    p_demo.add_argument("--port", type=int, default=5001, help="Port for web server")
    p_demo.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    p_demo.add_argument("--keep", action="store_true", help="Keep demo library after exit")
    p_demo.add_argument("--debug", action="store_true", help="Enable debug logging")

    # --- pick (power-user one-liner) ---
    p_pick = sub.add_parser("pick", help="One-liner: score, select, and optionally export")
    p_pick.add_argument("library", help="Path to library directory")
    p_pick.add_argument("--top", "-k", type=int, default=50, help="Number of photos to select")
    p_pick.add_argument(
        "--boost-face",
        action="append",
        default=[],
        dest="boost_face",
        help="Boost a named person (repeatable)",
    )
    p_pick.add_argument("--out", help="Export selected photos to this directory")
    output_fmt = p_pick.add_mutually_exclusive_group()
    output_fmt.add_argument("--json", action="store_true", help="Output as JSON")
    output_fmt.add_argument("--paths-only", action="store_true", help="Output filepaths only")
    p_pick.add_argument(
        "--quality",
        choices=("original", "high", "medium", "low"),
        default="original",
        help="Export JPEG quality preset (default: original)",
    )
    p_pick.add_argument("--dry-run", action="store_true", help="Show selection without exporting")

    # --- db restore-backup (recovery from a bad migration) ---
    p_model = sub.add_parser(
        "model",
        help=(
            "Model registry + restricted-license acceptance (text-mode parity with the GUI dialog)"
        ),
    )
    model_sub = p_model.add_subparsers(dest="model_command")
    from bpp.commands.model import add_subparsers as _add_model_subparsers

    _add_model_subparsers(model_sub)

    p_db = sub.add_parser(
        "db",
        help="Database utilities (restore from backup, etc.)",
    )
    db_sub = p_db.add_subparsers(dest="db_command")
    p_restore = db_sub.add_parser(
        "restore-backup",
        help="Restore the library DB from .backup (or .backup.prev)",
        description=(
            "Recovery path for a failed schema migration or DB "
            "corruption. The current DB is moved aside with a "
            "timestamped suffix; .backup is verified for integrity, "
            "then copied into place. Use --previous to restore from "
            ".backup.prev (the older generation) instead."
        ),
    )
    p_restore.add_argument(
        "--library",
        required=True,
        help="Path to the library directory (the one passed to `bpp serve --library`)",
    )
    p_restore.add_argument(
        "--previous",
        action="store_true",
        help="Restore from .backup.prev (older snapshot) instead of .backup",
    )
    p_restore.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation",
    )
    p_restore.add_argument(
        "--accept-stale",
        action="store_true",
        help=(
            "Allow restoring from a backup older than 7 days when "
            "combined with --yes. Without this flag, --yes refuses "
            "stale backups so automation can't silently destroy "
            "weeks of work."
        ),
    )
    p_restore.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass the running-server lockfile check. Use only "
            "when you're SURE no `bpp serve` / desktop app is "
            "running against this library — overwriting the DB "
            "with a server attached corrupts state silently."
        ),
    )

    return parser


def cmd_analyze(args: argparse.Namespace) -> int:
    """Scan images, extract features, and compute quality scores."""
    from bpp.commands import do_analyze

    return do_analyze(args)


def cmd_select(args: argparse.Namespace) -> int:
    """Select the best photos from previously analyzed data."""
    from bpp.commands import do_select

    return do_select(args)


def cmd_run(args: argparse.Namespace) -> int:
    """Analyze and select in one shot."""
    from bpp.commands import do_run

    return do_run(args)


def cmd_web(args: argparse.Namespace) -> int:
    """Launch the interactive web UI (alias for serve)."""
    from bpp.commands import do_web

    return do_web(args)


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the photo management server."""
    from bpp.commands import do_serve

    return do_serve(args)


def cmd_demo(args: argparse.Namespace) -> int:
    """Generate sample photos and launch the web UI for a quick demo."""
    from bpp.commands import do_demo

    return do_demo(args)


def cmd_pick(args: argparse.Namespace) -> int:
    """One-liner: score, select, and optionally export the best photos."""
    from bpp.commands import do_pick

    return do_pick(args)


def cmd_model(args: argparse.Namespace) -> int:
    """Dispatch `bpp model ...` to the right sub-handler set up by
    :func:`bpp.commands.model.add_subparsers`."""
    func = getattr(args, "_model_func", None)
    if func is None:
        print(
            "Usage: bpp model <subcommand>  (try `bpp model --help`)",
            file=sys.stderr,
        )
        return 1
    return func(args)


def cmd_db(args: argparse.Namespace) -> int:
    """Database utility commands (restore-backup, ...)."""
    if args.db_command == "restore-backup":
        from bpp.commands import do_db_restore_backup

        return do_db_restore_backup(args)
    print("Usage: bpp db <subcommand>  (try `bpp db --help`)", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    dispatch = {
        "analyze": cmd_analyze,
        "select": cmd_select,
        "run": cmd_run,
        "web": cmd_web,
        "serve": cmd_serve,
        "demo": cmd_demo,
        "pick": cmd_pick,
        "model": cmd_model,
        "db": cmd_db,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
