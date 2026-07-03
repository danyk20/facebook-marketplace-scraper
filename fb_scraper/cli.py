#!/usr/bin/env python3
"""
Facebook Marketplace scraper CLI - no API key, no token, no paid scraping
service. See fb_scraper/scraper.py for how it works and why it drives a
real browser instead of calling an API (unlike its sibling project,
AutoScout24Scraper, which found a public JSON API to call directly).

    facebook-marketplace-scraper --query "Tesla Model S"
    facebook-marketplace-scraper --query "Tesla Model S" --out tesla
    facebook-marketplace-scraper --query "iPhone 15" --no-detail
    facebook-marketplace-scraper --query "Tesla Model S" --price-to 30000 --year-from 2018

Login is effectively required (see fb_scraper/browser.py). Either:
    facebook-marketplace-scraper --query "Tesla Model S" --headed          # log in by hand, once
    facebook-marketplace-scraper --query "Tesla Model S" --email you@example.com --password -   # prompts for it
    FB_EMAIL=you@example.com FB_PASSWORD=... facebook-marketplace-scraper --query "Tesla Model S"

As a library, from another project:

    from fb_scraper.scraper import scrape
    result = scrape("Tesla Model S", price_to=30000)
    for row in result.rows:
        print(row["price"], row["url"])
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

from playwright.sync_api import Error as PlaywrightError

from fb_scraper import __version__, config
from fb_scraper.browser import LoginFailedError
from fb_scraper.scraper import LoginRequiredError, MarketplaceConsentRequiredError, scrape

logger = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape Facebook Marketplace for a given search query.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--query", required=True, help="Free text search, e.g. 'Tesla Model S' or 'iPhone 15'")
    parser.add_argument(
        "--country",
        default=config.DEFAULT_COUNTRY,
        help=f"Country to search (default: {config.DEFAULT_COUNTRY!r}). Only 'ch' is "
        f"implemented today - see fb_scraper/config.py.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output file base name (without extension). Defaults to a slug of --query in the current directory.",
    )
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="Skip visiting each listing's own page; keep only the summary fields "
        "from the search results (faster, fewer fields).",
    )
    parser.add_argument(
        "--all-countries",
        action="store_true",
        help="Don't filter out listings that don't look like they're in --country.",
    )
    parser.add_argument(
        "--headed", action="store_true", help="Show the browser. Useful for the first run to log in - see README."
    )
    parser.add_argument("--delay", type=float, default=0.4, help="Delay in seconds between detail-page visits.")
    parser.add_argument("--price-from", type=int, default=None, help="Minimum price, inclusive.")
    parser.add_argument("--price-to", type=int, default=None, help="Maximum price, inclusive.")
    parser.add_argument("--mileage-from", type=int, default=None, help="Minimum mileage in km, inclusive (vehicles).")
    parser.add_argument("--mileage-to", type=int, default=None, help="Maximum mileage in km, inclusive (vehicles).")
    parser.add_argument(
        "--year-from", type=int, default=None, help="Earliest first-registration year, inclusive (vehicles)."
    )
    parser.add_argument(
        "--year-to", type=int, default=None, help="Latest first-registration year, inclusive (vehicles)."
    )
    parser.add_argument(
        "--condition",
        default=None,
        help="Comma-separated item condition filter, e.g. 'new,used_like_new'. "
        "Valid values: new, used_like_new, used_good, used_fair.",
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Facebook login email, used if not already logged in. Defaults to the "
        "FB_EMAIL environment variable if not given.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Facebook login password, used if not already logged in. Pass '-' to be "
        "prompted for it instead of putting it in shell history/process list. "
        "Defaults to the FB_PASSWORD environment variable if not given.",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="Show debug-level detail, including every browser action taken."
    )
    verbosity.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress progress output; only warnings/errors are shown."
    )
    return parser


def _configure_cli_logging(*, verbose: bool, quiet: bool) -> None:
    """Set up console logging for CLI use, matching this project's
    traditional print()-based output split: progress (INFO, or DEBUG with
    -v) goes to stdout, warnings/errors (-q still shows these) go to
    stderr. Only main() calls this - plain library use of scrape() never
    touches logging config, since that would be rude to whatever
    application imported it (see fb_scraper/__init__.py)."""
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    plain = logging.Formatter("%(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.addFilter(lambda record: record.levelno < logging.WARNING)
    stdout_handler.setFormatter(plain)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(plain)

    package_logger = logging.getLogger("fb_scraper")
    package_logger.handlers.clear()
    package_logger.addHandler(stdout_handler)
    package_logger.addHandler(stderr_handler)
    package_logger.setLevel(level)
    package_logger.propagate = False


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_") or "listings"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Parses argv (defaults to sys.argv[1:]), scrapes, and
    writes CSV + JSON files. Returns 0 on success; lets exceptions propagate
    (see run_cli() for the error-handling / exit-code wrapper used by the
    console-script/__main__ entry points)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    _configure_cli_logging(verbose=args.verbose, quiet=args.quiet)

    condition = args.condition.split(",") if args.condition else None

    email = args.email or os.environ.get("FB_EMAIL")
    password = args.password or os.environ.get("FB_PASSWORD")
    if password == "-":
        password = getpass.getpass("Facebook password: ")

    result = scrape(
        args.query,
        country=args.country,
        detail=not args.no_detail,
        min_price=args.price_from,
        max_price=args.price_to,
        min_mileage=args.mileage_from,
        max_mileage=args.mileage_to,
        min_year=args.year_from,
        max_year=args.year_to,
        condition=condition,
        local_only=not args.all_countries,
        delay=args.delay,
        verbose=True,
        headless=not args.headed,
        email=email,
        password=password,
    )

    out_base = args.out or _slug(args.query)
    csv_path = f"{out_base}.csv"
    json_path = f"{out_base}.json"
    result.to_csv(csv_path)
    result.to_json(json_path)

    logger.info("\nDone. %d unique listings found.", len(result.rows))
    logger.info("  CSV:  %s", csv_path)
    logger.info("  JSON: %s", json_path)
    return 0


def run_cli(argv: list[str] | None = None) -> int:
    """Run main() and translate exceptions into (message, exit code) the way
    the command line expects. Factored out from the console-script/__main__
    entry points so it can be unit-tested directly without spawning a
    subprocess."""
    try:
        return main(argv) or 0
    except (LoginRequiredError, LoginFailedError, MarketplaceConsentRequiredError) as exc:
        logger.error("Error: %s", exc)
        return 1
    except ValueError as exc:
        logger.error("Error: %s", exc)
        return 1
    except PlaywrightError as exc:
        logger.error("Browser/network error talking to facebook.com: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("\nInterrupted.")
        return 130


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests/test_e2e.py
    sys.exit(run_cli())
