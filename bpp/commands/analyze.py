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

from bpp.config import load_config
from bpp.utils.logging import get_logger, setup_logging


def do_analyze(args: argparse.Namespace) -> int:
    setup_logging(debug=getattr(args, "debug", False))
    log = get_logger(__name__)

    # load plugin entry-points before consuming the scoring
    # registry. Idempotent under repeated calls within a process.
    from bpp.plugins import load_plugin_entry_points

    load_plugin_entry_points()

    cfg = load_config(getattr(args, "config", None))
    extensions = [e.strip().lower() for e in args.extensions.split(",")]

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
            follow_symlinks=cfg.get("follow_symlinks", False),
            max_images=args.max,
        )

    if not images:
        log.warning("No images found in %s", input_path)
        return 0

    log.info("Found %d images to analyze", len(images))

    if args.dry_run:
        log.info("[dry-run] Would analyze %d images", len(images))
        return 0

    with timer.section("analyze"):
        results = analyze_all(
            images,
            workdir=workdir,
            config=cfg,
            workers=args.workers,
            seed=args.seed,
        )

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

    cfg = load_config(getattr(args, "config", None))

    from bpp.dedupe.cluster import deduplicate
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

    with timer.section("load"):
        analysis = load_analysis(workdir)

    if not analysis:
        log.error("No analysis data found in %s. Run 'analyze' first.", workdir)
        return 1

    log.info("Loaded analysis for %d images", len(analysis))

    with timer.section("dedupe"):
        candidates = deduplicate(analysis, config=cfg)

    log.info("After dedup: %d candidates from %d clusters", len(candidates), len(analysis))

    with timer.section("select"):
        selected = choose(candidates, k=args.k, config=cfg, seed=args.seed)

    log.info("Selected %d photos", len(selected))

    if args.dry_run:
        log.info("[dry-run] Would export %d photos to %s", len(selected), outdir)
        return 0

    with timer.section("export"):
        export_selected(
            selected,
            analysis=analysis,
            outdir=outdir,
            mode=args.export_mode,
            gallery=args.gallery,
            config=cfg,
        )

    log.info("Output written to %s", outdir)
    timer.summary()
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
        seed=args.seed,
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
        seed=args.seed,
        extensions=args.extensions,
    )
    return do_select(select_ns)
