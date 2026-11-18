"""`bpp analyze`, `bpp select`, `bpp run` — the scoring pipeline trio.

Extracted from bpp.commands during the v0.1 cleanup. Re-exported from
`bpp.commands` for backwards compatibility with the CLI (`bpp.cli`)
and tests that still import `from bpp.commands import do_analyze`.
`do_run` is just `do_analyze` + `do_select` glue, so they live
together to keep cross-calls local.
"""

from __future__ import annotations

import argparse
import os

from bpp.config import cfg_get, load_config, parse_scan_extensions
from bpp.utils.logging import get_logger, setup_logging


def _hold_library(workdir: str, log) -> list[str] | None:
    """Take the library's lock for this run, as the app does while it has
    the library open: the two never write its analysis at the same time
    (a delete made in the app while this run is still going would be
    undone by what it writes). The folders locked, or None (after saying
    why) when the app or another run has it."""
    from bpp.utils.serving_lock import ServingLockError, acquire_lock, clear_lock

    folders = [workdir]
    data = os.path.join(workdir, "data")
    if os.path.isfile(os.path.join(data, "photopicker.db")):
        folders.append(data)  # a library's root: the app keeps its lock in data/
    held: list[str] = []
    for folder in folders:
        try:
            holder = acquire_lock(folder)
        except ServingLockError as exc:
            log.error("Can't analyse into %s: %s", folder, exc)
            holder = -1
        if holder is not None:
            if holder > 0:
                log.error(
                    "The app (or another analysis) has this library open (process %d). "
                    "Close it, or analyse from the app, then run this again.",
                    holder,
                )
            for done in held:
                clear_lock(done)
            return None
        held.append(folder)
    return held


def do_analyze(args: argparse.Namespace) -> int:
    setup_logging(debug=getattr(args, "debug", False))
    log = get_logger(__name__)

    # load plugin entry-points before consuming the scoring
    # registry. Idempotent under repeated calls within a process.
    from bpp.plugins import load_plugins_for_cli

    load_plugins_for_cli()

    cfg = load_config(getattr(args, "config", None))
    extensions = parse_scan_extensions(args.extensions or cfg_get(cfg, "scan_extensions"))

    from bpp.io_scan import scan_images
    from bpp.scoring.aggregate import analyze_all
    from bpp.utils.timing import Timer

    timer = Timer()

    input_path = os.path.abspath(args.input)
    workdir = os.path.abspath(args.out)

    if not os.path.isdir(input_path):
        log.error("Input path does not exist or is not a directory: %s", input_path)
        return 1

    os.makedirs(workdir, exist_ok=True)

    with timer.section("scan"):
        images = scan_images(
            input_path,
            extensions=extensions,
            follow_symlinks=cfg_get(cfg, "follow_symlinks"),
            max_images=args.max,
        )

    if not images:
        log.warning("No images found in %s", input_path)
        return 0

    log.info("Found %d images to analyze", len(images))

    if args.dry_run:
        log.info("[dry-run] Would analyze %d images", len(images))
        return 0

    from bpp.scoring.model_base import ModelIntegrityError

    held = _hold_library(workdir, log)
    if held is None:
        return 1
    try:
        with timer.section("analyze"):
            results = analyze_all(
                images,
                workdir=workdir,
                config=cfg,
                workers=args.workers,
            )
    except ModelIntegrityError as exc:
        log.error(
            "Analysis stopped: a model file failed its integrity check (%s). "
            "Re-download it in Settings → Models, or delete the damaged file so "
            "it's fetched again, then re-run.",
            exc,
        )
        return 1
    except RuntimeError as exc:
        # e.g. a worker process died: say so plainly, exit non-zero.
        log.error("Analysis stopped: %s", exc)
        return 1
    finally:
        from bpp.utils.serving_lock import clear_lock

        for folder in held:
            clear_lock(folder)

    log.info(
        "Analysis complete: %d processed, %d skipped",
        results["processed"],
        results["skipped"],
    )
    log.info("Workdir: %s", workdir)
    timer.summary()
    return 0


def do_select(args: argparse.Namespace) -> int:
    setup_logging(debug=False)
    log = get_logger(__name__)

    # Plugins first: a dedupe strategy named in config may be one of theirs.
    from bpp.plugins import load_plugins_for_cli

    load_plugins_for_cli()

    cfg = load_config(getattr(args, "config", None))

    from bpp.dedupe.select import dedupe_candidates
    from bpp.output.export import export_selected
    from bpp.scoring.aggregate import load_analysis
    from bpp.selection.choose import choose
    from bpp.utils.timing import Timer

    timer = Timer()

    workdir = os.path.abspath(args.workdir)
    outdir = os.path.abspath(args.out)

    if not os.path.isdir(workdir):
        log.error("Workdir does not exist: %s", workdir)
        return 1

    from bpp.utils.path_validation import split_existing_folders

    named_roots, missing = split_existing_folders(getattr(args, "source_root", None) or [])
    if missing:
        log.error("--source-root folder does not exist: %s", ", ".join(missing))
        return 1

    with timer.section("load"):
        # The note of photos deleted for good is in the library's data
        # folder (or its root): checked whichever of the two this is.
        notes = [os.path.join(workdir, "data")]
        if os.path.basename(workdir) == "data":
            notes.append(os.path.dirname(workdir))
        analysis = load_analysis(workdir, notes_in=tuple(notes))

    if not analysis:
        log.error("No analysis data found in %s. Run 'analyze' first.", workdir)
        return 1

    log.info("Loaded analysis for %d images", len(analysis))

    with timer.section("dedupe"):
        candidates, dedup_mode = dedupe_candidates(analysis, cfg)

    log.info(
        "After dedup (%s): %d candidates from %d photos", dedup_mode, len(candidates), len(analysis)
    )

    with timer.section("select"):
        selected = choose(candidates, k=args.k, config=cfg)

    log.info("Selected %d photos", len(selected))

    if args.dry_run:
        log.info("[dry-run] Would export %d photos to %s", len(selected), outdir)
        return 0

    with timer.section("export"):
        result = export_selected(
            selected,
            analysis=analysis,
            outdir=outdir,
            mode=args.export_mode,
            gallery=args.gallery,
            config=cfg,
            # Folders the owner named on this command line: never one the
            # workdir's own files supply.
            source_roots=[workdir, *named_roots],
        )

    log.info("Output written to %s", outdir)
    timer.summary()
    if result.outside_source:
        log.error(
            "%d photo(s) were not exported: their files are outside %s. "
            "Name the folder they were analyzed from with --source-root DIR.",
            result.outside_source,
            workdir,
        )
        return 1
    return 0


def do_run(args: argparse.Namespace) -> int:
    """One-shot: analyze + select."""
    setup_logging(debug=getattr(args, "debug", False))

    # Create a temporary workdir
    outdir = os.path.abspath(args.out)
    workdir = os.path.join(outdir, ".workdir")

    # Build analyze args
    analyze_ns = argparse.Namespace(
        input=args.input,
        out=workdir,
        config=getattr(args, "config", None),
        max=args.max,
        workers=args.workers,
        debug=getattr(args, "debug", False),
        dry_run=args.dry_run,
        extensions=args.extensions,
    )
    rc = do_analyze(analyze_ns)
    if rc != 0:
        return rc

    if args.dry_run:
        return 0

    select_ns = argparse.Namespace(
        workdir=workdir,
        k=args.k,
        out=outdir,
        config=getattr(args, "config", None),
        export_mode=args.export_mode,
        gallery=getattr(args, "gallery", False),
        dry_run=args.dry_run,
        extensions=args.extensions,
        source_root=[os.path.abspath(args.input), *(getattr(args, "source_root", None) or [])],
    )
    return do_select(select_ns)
