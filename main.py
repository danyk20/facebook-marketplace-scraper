#!/usr/bin/env python3
"""
Facebook Marketplace scraper - no API key, no token, no paid scraping
service. See fb_scraper/scraper.py for how it works and why it drives a
real browser instead of calling an API (unlike its sibling project,
AutoScout24Scraper, which found a public JSON API to call directly).

    python3 main.py --query "Tesla Model S"
    python3 main.py --query "Tesla Model S" --out tesla
    python3 main.py --query "iPhone 15" --no-detail
    python3 main.py --query "Tesla Model S" --price-to 30000 --year-from 2018

Login is effectively required (see fb_scraper/browser.py). Either:
    python3 main.py --query "Tesla Model S" --headed          # log in by hand, once
    python3 main.py --query "Tesla Model S" --email you@example.com --password -   # prompts for it
    FB_EMAIL=you@example.com FB_PASSWORD=... python3 main.py --query "Tesla Model S"

As a library, from another project:

    from fb_scraper.scraper import scrape
    result = scrape("Tesla Model S", price_to=30000)
    for row in result.rows:
        print(row["price"], row["url"])
"""
import os
import sys
import argparse
import getpass

from playwright.sync_api import Error as PlaywrightError

from fb_scraper import config
from fb_scraper.browser import LoginFailedError
from fb_scraper.scraper import LoginRequiredError, MarketplaceConsentRequiredError, scrape


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Scrape Facebook Marketplace for a given search query.")
    parser.add_argument("--query", required=True, help="Free text search, e.g. 'Tesla Model S' or 'iPhone 15'")
    parser.add_argument("--country", default=config.DEFAULT_COUNTRY,
                         help=f"Country to search (default: {config.DEFAULT_COUNTRY!r}). Only 'ch' is "
                              f"implemented today - see fb_scraper/config.py.")
    parser.add_argument("--out", default=None, help="Output file base name (without extension). "
                                                      "Defaults to a slug of --query in the current directory.")
    parser.add_argument("--no-detail", action="store_true",
                         help="Skip visiting each listing's own page; keep only the summary fields "
                              "from the search results (faster, fewer fields).")
    parser.add_argument("--all-countries", action="store_true",
                         help="Don't filter out listings that don't look like they're in --country.")
    parser.add_argument("--headed", action="store_true",
                         help="Show the browser. Useful for the first run to log in - see README.")
    parser.add_argument("--delay", type=float, default=0.4, help="Delay in seconds between detail-page visits.")
    parser.add_argument("--price-from", type=int, default=None, help="Minimum price, inclusive.")
    parser.add_argument("--price-to", type=int, default=None, help="Maximum price, inclusive.")
    parser.add_argument("--mileage-from", type=int, default=None, help="Minimum mileage in km, inclusive (vehicles).")
    parser.add_argument("--mileage-to", type=int, default=None, help="Maximum mileage in km, inclusive (vehicles).")
    parser.add_argument("--year-from", type=int, default=None,
                         help="Earliest first-registration year, inclusive (vehicles).")
    parser.add_argument("--year-to", type=int, default=None,
                         help="Latest first-registration year, inclusive (vehicles).")
    parser.add_argument("--condition", default=None,
                         help="Comma-separated item condition filter, e.g. 'new,used_like_new'. "
                              "Valid values: new, used_like_new, used_good, used_fair.")
    parser.add_argument("--email", default=None,
                         help="Facebook login email, used if not already logged in. Defaults to the "
                              "FB_EMAIL environment variable if not given.")
    parser.add_argument("--password", default=None,
                         help="Facebook login password, used if not already logged in. Pass '-' to be "
                              "prompted for it instead of putting it in shell history/process list. "
                              "Defaults to the FB_PASSWORD environment variable if not given.")
    return parser


def _slug(text):
    return "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_") or "listings"


def main(argv=None):
    """CLI entry point. Parses argv (defaults to sys.argv[1:]), scrapes, and
    writes CSV + JSON files. Returns 0 on success; lets exceptions propagate
    (see run_cli() for the error-handling / exit-code wrapper used by the
    __main__ guard below)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    condition = args.condition.split(",") if args.condition else None

    email = args.email or os.environ.get("FB_EMAIL")
    password = args.password or os.environ.get("FB_PASSWORD")
    if password == "-":
        password = getpass.getpass("Facebook password: ")

    result = scrape(
        args.query,
        country=args.country,
        detail=not args.no_detail,
        min_price=args.price_from, max_price=args.price_to,
        min_mileage=args.mileage_from, max_mileage=args.mileage_to,
        min_year=args.year_from, max_year=args.year_to,
        condition=condition,
        local_only=not args.all_countries,
        delay=args.delay, verbose=True, headless=not args.headed,
        email=email, password=password,
    )

    out_base = args.out or _slug(args.query)
    csv_path = f"{out_base}.csv"
    json_path = f"{out_base}.json"
    result.to_csv(csv_path)
    result.to_json(json_path)

    print(f"\nDone. {len(result.rows)} unique listings found.")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")
    return 0


def run_cli(argv=None):
    """Run main() and translate exceptions into (message, exit code) the way
    the command line expects. Factored out from the __main__ guard so it can
    be unit-tested directly without spawning a subprocess."""
    try:
        return main(argv) or 0
    except (LoginRequiredError, LoginFailedError, MarketplaceConsentRequiredError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except PlaywrightError as exc:
        print(f"Browser/network error talking to facebook.com: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests/test_e2e.py
    sys.exit(run_cli())
