# Facebook Marketplace Scraper

[![CI](https://github.com/danyk20/facebook-marketplace-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/danyk20/facebook-marketplace-scraper/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/facebook-marketplace-scraper)](https://pypi.org/project/facebook-marketplace-scraper/)
[![Coverage](https://img.shields.io/badge/unit%20test%20coverage-96%25-brightgreen)](#testing)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue)](https://www.python.org/)

> Unofficial, independently developed project. Not affiliated with,
> endorsed by, or sponsored by Meta Platforms, Inc. "Facebook" and
> "Marketplace" are trademarks of Meta Platforms, Inc.

Fetches every listing matching a free-text search from Facebook Marketplace —
no API key, no token, no paid scraping service. Defaults to Switzerland
(`--country ch`), searching from Zurich with a radius wide enough to cover
the whole country. Works for any item type (cars, phones, computers,
furniture, ...), not just vehicles.

**AI-agent friendly.** This project is released under the permissive
[MIT license](LICENSE) and is meant to be run, imported, or adapted by AI
agents just as a human developer would. The [library reference](#as-a-library)
and [Data structure](#data-structure) sections are written to be a complete
spec on their own, without needing to read the source first.

## How it works

Facebook Marketplace blocks plain HTTP scraping (bot-fingerprint detection,
no public API), so this scraper drives a real headless Chromium browser via
[Playwright](https://playwright.dev/).

**Login is required** — anonymous visitors get redirected straight to the
login page. Three ways to authenticate (see `fb_scraper/browser.py`):

1. **Credentials** — pass `--email`/`--password` (or `FB_EMAIL`/`FB_PASSWORD`
   env vars). Doesn't work through a 2FA/checkpoint challenge — use option 2
   for that. The account also needs to have clicked through Marketplace's
   one-time data-usage consent dialog at least once (see below); do that by
   hand first with option 2.
2. **By hand** — run once with `--headed` and log in yourself in the window
   that opens. Handles 2FA and the consent dialog fine, since a human is
   there.
3. **Do nothing** — a previous login is reused automatically via a
   persistent browser profile at `~/.fb_scraper/browser_profile` (override
   with `FB_SCRAPER_PROFILE_DIR`).

A logged-in EU/EEA account may also hit Facebook's Marketplace data-usage
**consent screen** once. This is a privacy decision, so the scraper never
clicks through it automatically — it raises `MarketplaceConsentRequiredError`
telling you to run `--headed` and choose the **personalised profile** option
yourself, once. Both login and consent are then remembered for future runs.

**Two-phase scraping**: the search page only gives title/price/location/
thumbnail per listing; visiting each listing's own page
(`fetch_detail()`/`visit_all_listings()`, on by default) additionally
extracts condition, description, post date, the full photo gallery, and
seller info (name, photo, join date, and — unless `--no-seller-listings` —
their other current listings). Results are sorted by price ascending and
de-duplicated by listing id.

Most private sellers only put details like mileage or year in their
free-text `description` — Facebook doesn't expose them as separate fields,
so this scraper doesn't invent structure that isn't there.

**Language-independent**: the detail page is parsed by DOM shape (e.g. the
title is always an `<h1>`), not by matching translated words, so it works
regardless of the logged-in account's UI language. Tested against English,
German, and French. The one known gap is rental listings, which use a
different page layout and fall back to an English/German word-matching
parser — see `is_rental` below.

## Countries

Every search needs a city to anchor on, with a radius. `fb_scraper/config.py`'s
`COUNTRY_ANCHORS` maps a country code to an anchor city + radius:

```python
COUNTRY_ANCHORS = {
    "ch": {"slug": "zurich", "radius_km": 500},
}
```

**Only `"ch"` is configured today.** Adding a country is a one-line addition
to `COUNTRY_ANCHORS` — passing an unconfigured `--country` fails immediately
with a clear error listing what's available.

## Setup

**To use it** in your own project:

```bash
pip install facebook-marketplace-scraper
python -m playwright install chromium
```

**To develop this repo** (run it from source, run tests, hack on the code),
use [pipenv](https://pipenv.pypa.io/) instead:

```bash
pipenv install --dev
pipenv run python -m playwright install chromium
```

Then run the app the same way you would after a `pip install`, just prefixed
with `pipenv run`:

```bash
pipenv run python main.py --query "Tesla Model S"
```

Or run the test suite:

```bash
pipenv run pytest
```

(`--dev` also installs pytest, ruff, mypy, build, and twine — leave it off
if you only want to run the scraper, not develop it.)

## Usage

### As a CLI

**First run — log in** (see [How it works](#how-it-works)):

```bash
# Option A: credentials
facebook-marketplace-scraper --query "Tesla Model S" --email you@example.com --password -
# ('-' prompts for the password instead of exposing it in shell history)

# Option B: log in by hand in a real browser window
facebook-marketplace-scraper --query "Tesla Model S" --headed
```

**Every run after that** reuses the saved session:

```bash
facebook-marketplace-scraper --query "Tesla Model S"
```

This writes `tesla_model_s.csv` and `tesla_model_s.json` in the current
directory. If you see `LoginRequiredError`, re-run with credentials or
`--headed`. If you see `MarketplaceConsentRequiredError`, re-run with
`--headed` and click through Facebook's consent screen once.

### Options

| Flag | Description |
|---|---|
| `--query` | Free text search, e.g. `"Tesla Model S"` (required) |
| `--country` | Country to search (default `ch`) |
| `--out` | Output file base name, without extension. Defaults to a slug of `--query` |
| `--no-detail` | Skip visiting each listing's own page; keep only summary fields |
| `--no-seller-listings` | Skip the seller's "other listings" popup (faster) |
| `--all-countries` | Don't filter out listings outside `--country` |
| `--headed` | Show the browser (for first login or the consent screen) |
| `--email` / `--password` | Facebook login, or `FB_EMAIL`/`FB_PASSWORD` env vars |
| `--delay` | Seconds between detail-page visits (default `0.4`) |
| `--price-from` / `--price-to` | Filter by price, inclusive |
| `--mileage-from` / `--mileage-to` | Filter by mileage in km — vehicles only |
| `--year-from` / `--year-to` | Filter by first-registration year — vehicles only |
| `--condition` | Comma-separated: `new`, `used_like_new`, `used_good`, `used_fair` |
| `--version` | Print the installed version and exit |
| `-v` / `-q` | Verbose / quiet output |

### Examples

```bash
facebook-marketplace-scraper --query "Tesla Model S" --price-to 30000
facebook-marketplace-scraper --query "Tesla Model S" --year-from 2018 --mileage-to 60000
facebook-marketplace-scraper --query "iPhone 15" --condition new,used_like_new
facebook-marketplace-scraper --query "MacBook Pro" --no-detail
```

### As a library

```python
from fb_scraper.scraper import scrape

result = scrape("Tesla Model S", max_price=30000, min_year=2018)

result.rows          # list[dict]: one flattened dict per listing, CSV-ready
result.listings      # list[dict]: one dict per listing, see Data structure
result.query, result.country, result.total_elements

for row in result.rows:
    print(row["price"], row["condition"], row["url"])

result.to_csv("tesla_model_s.csv")   # optional — scrape() itself writes nothing
result.to_json("tesla_model_s.json")
```

`scrape()` raises `ValueError` for bad input (before opening a browser),
`LoginRequiredError` if not logged in, `MarketplaceConsentRequiredError` if
the consent screen hasn't been cleared, and `LoginFailedError` if
credentials didn't work (wrong password, or a 2FA challenge needing a human).

By default the library logs nothing (it never calls `logging.basicConfig()`
or attaches handlers). To see progress:

```python
import logging
logging.basicConfig(level=logging.INFO)
```

## Data structure

### JSON (`result.listings`)

Every listing always includes:

| Field | Type | Description |
|---|---|---|
| `listing_id` | `string` | Facebook's internal listing id |
| `title` | `string \| null` | Free-text headline (often absent) |
| `price` | `string \| null` | As rendered, e.g. `"29'900 CHF"` |
| `original_price` | `string \| null` | Price before the seller lowered it, same format; `null` if never lowered |
| `location` | `string \| null` | `"City, Region"`, e.g. `"Zürich, ZH"` |
| `is_local` | `bool` | Whether `location` looks like it's inside `country` |
| `country` | `string` | The searched country, e.g. `"ch"` |
| `url` | `string` | Full URL of the listing |
| `image_url` | `string \| null` | Thumbnail URL from the search result |

Additionally, when `detail=True` (the default):

| Field | Type | Description |
|---|---|---|
| `condition` | `string \| null` | Facebook's own wording, not normalized |
| `description` | `string \| null` | Full free-text description |
| `posted_at` | `string \| null` | Relative post date, e.g. `"vor 3 Wochen"` |
| `images` | `list[string]` | Full-size photo URLs, in order |
| `category` | `string \| null` | Category slug, e.g. `"propertyrentals"` |
| `is_rental` | `bool` | Whether this is a rental listing |
| `price_period` | `string \| null` | Rental period, e.g. `"month"` |
| `seller_name` | `string \| null` | Seller's display name |
| `seller_profile_url` | `string \| null` | Link to the seller's Marketplace profile |
| `seller_photo_url` | `string \| null` | Seller's profile photo URL |
| `seller_joined` | `string \| null` | e.g. `"Joined Facebook in 2020"` |
| `seller_listing_count` | `int \| null` | Seller's current listing count (`null` if not fetched) |
| `seller_listing_urls` | `list[string]` | Links to the seller's current listings |

There's no fixed schema published by Facebook — treat missing fields
defensively (`.get(...)`, not `[...]`).

### CSV (`result.rows`)

A flattened version of the same data, one row per listing, sorted by price
ascending. List fields (`images`, `seller_listing_urls`) are joined with
`"; "`. Columns are the union of fields seen across all rows, with the core
fields pinned first. If there are zero rows, no CSV is written (the JSON
file still is, as `[]`).

## Relationship to AutoScout24Scraper

Designed to be used interchangeably with
[AutoScout24Scraper](https://github.com/danyk20/autoscout24-scraper) — same
`scrape() -> ScrapeResult` shape, same CLI conventions, same testing
approach. Key differences:

| | AutoScout24Scraper | This project |
|---|---|---|
| Transport | `requests` against a public JSON API | Playwright/Chromium (no API exists) |
| Search parameter | `make` + `model` | `query` (free text) |
| Region parameter | `domain` (e.g. `ch`, `de`) | `country` (anchor city + radius) |
| Detail fields | Structured JSON (VIN, specs, ...) | Mostly free-text `description` |
| Unit test mocking | [`responses`](https://github.com/getsentry/responses) | `BrowserContext.route()` |

## Testing

```bash
pipenv run pytest                          # unit tests only (default), with coverage
pipenv run pytest -m e2e --no-cov          # end-to-end tests against real facebook.com
pipenv run pytest -m "e2e or not e2e" --no-cov  # everything
pipenv run ruff check . && pipenv run ruff format --check . && pipenv run mypy
```

Unit tests (`tests/test_*.py`, excluding `test_e2e.py`) mock the network via
Playwright's `BrowserContext.route()` — a real headless Chromium runs, but
nothing reaches facebook.com. End-to-end tests (`tests/test_e2e.py`, marked
`@pytest.mark.e2e`, excluded by default) make real calls, targeting "Tesla
Roadster" since it reliably has zero listings in Switzerland, keeping the
run fast without hammering the site.

Coverage is currently **96%** of `fb_scraper/` (floor enforced at 90%).

## Notes

- Be a reasonable citizen: don't remove the delay between detail-page visits
  or crank up concurrency — this scrapes real pages, not a rate-limited API.
- Repeatedly retrying credential logins in a short time can trigger extra
  Facebook verification. If that happens, stop retrying and use `--headed`
  to clear it by hand.
- If facebook.com changes its markup, look at `listing_from_json()`/
  `search_listings()`, `parse_tile()` (fallback for search tiles with no
  JSON behind them), `_parse_detail_text()`/`fetch_detail()`, and
  `build_search_url()` in
  `fb_scraper/scraper.py` — each has a docstring on how it was
  reverse-engineered.
- `fb_scraper/storage.py` is an optional SQLite-backed helper for tracking
  which listings are new since your last run of the same search.

## License

[MIT](LICENSE) — free to use, copy, modify, and distribute, for any purpose,
with no warranty. AI agents and coding assistants are explicitly welcome to
use this project under the same terms as a human would.

This license doesn't grant any rights to Facebook/Meta's own data or terms
of service — this project only automates requests to public pages any
visitor's browser can already load; what you do with the results is between
you and them.
