"""CLI: python -m murraywater refresh [--no-open] [--backfill-from 2004] [--full] [--skip-waterfind]
         python -m murraywater render [--no-open]
         python -m murraywater export [--no-pdf]
         python -m murraywater publish [--out site]"""

import argparse
import sys

from . import config, refresh


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="murraywater", description="NSW Murray water market dashboard")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("refresh", help="fetch all sources, store, and render the dashboard")
    run.add_argument("--no-open", action="store_true", help="don't open the dashboard afterwards")
    run.add_argument("--backfill-from", type=int, default=config.REGISTER_FIRST_SEASON, metavar="YEAR",
                     help="first season start year for register history (default 2004 = 2004-05)")
    run.add_argument("--full", action="store_true", help="refetch every non-final register range")
    run.add_argument("--skip-waterfind", action="store_true", help="skip the Waterfind indicator")
    show = sub.add_parser("render", help="rebuild the dashboard from stored data without fetching")
    show.add_argument("--no-open", action="store_true", help="don't open the dashboard afterwards")
    share = sub.add_parser("export", help="write a shareable review copy (no Waterfind data) to output/review")
    share.add_argument("--no-pdf", action="store_true", help="HTML only, skip the PDF")
    site = sub.add_parser("publish", help="write the public web page (no Waterfind data) to a folder")
    site.add_argument("--out", default=str(config.ROOT / "site"), help="folder to write (default ./site)")
    args = parser.parse_args(argv)
    if args.command == "publish":
        from pathlib import Path
        return refresh.publish_site(Path(args.out))
    if args.command == "render":
        return refresh.render_only(no_open=args.no_open)
    if args.command == "export":
        return refresh.export_review(pdf=not args.no_pdf)
    return refresh.run(no_open=args.no_open, backfill_from=args.backfill_from, full=args.full,
                       skip_waterfind=args.skip_waterfind)


if __name__ == "__main__":
    sys.exit(main())
