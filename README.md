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

**Getting every listing**: Facebook's sort option changes *which* listings a
search returns, not just their order. Sorting by price returned only 2 of 64
"Tesla Model X" listings, and Facebook's default sort returned 39. So the
scraper always searches newest first (all 64), reads each batch of results
from the JSON Facebook sends while scrolling, and keeps scrolling until
Facebook says there are no more. Facebook also ends big searches early
("iPhone 15": 326 listings, but 400 when searched in two price ranges), so
a search returning 200+ listings is automatically searched again in smaller
price ranges and the results merged. That takes longer — about 1.5 minutes
instead of 20 seconds for "iPhone 15" — and `--no-price-split` turns it off.

Most private sellers only put details like mileage or year in their
free-text `description` — Facebook doesn't expose them as separate fields,
so this scraper doesn't invent structure that isn't there.

**No year or mileage filters**, on purpose. Facebook applies its own year
and mileage filters only to listings posted with structured vehicle data,
and silently drops every other listing. In testing, a year filter kept 38
of 358 "Tesla Model X" listings and dropped, for example, a 2017 Model X
whose year was only in its title. So the scraper always returns listings
of every year and mileage; `scrape()` still accepts `min_year`/`max_year`/
`min_mileage`/`max_mileage` so existing code keeps working, but ignores
them with a warning. Price filtering (`--price-from`/`--price-to`) is
unaffected: every listing has a price, and Facebook's price filter returned
exactly the same listings as filtering the full result set.

**Language-independent**: the detail page is parsed by DOM shape (e.g. the
title is always an `<h1>`), not by matching translated words, so it works
regardless of the logged-in account's UI language. Tested against English,
German, and French. The one known gap is rental listings, which use a
different page layout and fall back to an English/German word-matching
parser — see `is_rental` below.

## Countries

Every search needs a city to anchor on, with a radius. `fb_scraper/config.py`'s
`COUNTRY_ANCHORS` maps a country code to an anchor city + the radius to
search with (set on your account — see below):

```python
COUNTRY_ANCHORS = {
    "ch": {"slug": "zurich", "radius_km": 500},
}
```

**Only `"ch"` is configured today.** Adding a country is a one-line addition
to `COUNTRY_ANCHORS` — passing an unconfigured `--country` fails immediately
with a clear error listing what's available.

### The search radius comes from your Facebook account, not the URL

When logged in, Facebook ignores the `radius` URL parameter the scraper
sends. It uses the location radius saved on the account instead: the one
shown at the top of Marketplace as e.g. "Zürich · Within 250 km", which you
change under **Marketplace → Location**. That setting is account-wide, so
changing it in your normal browser changes what the scraper finds too.

Confirmed by testing (October 2026), with the account set to 250 km:

- Sending `radius=65`, `150` or `500` returned the **same** 357 "Tesla Model
  X" listings, including ones from Geneva, far outside 65 km of Zurich. The
  page even displayed "Within 65 km" while Facebook's own data said
  `filter_radius_km: 250`.
- Earlier the same day the same search returned only 64 listings, all from Zurich, Aargau and Zug. The 357
  included all 64 of those, plus listings from all over Switzerland (Vaud,
  Valais, Fribourg, Geneva, Bern, ...) - consistent with the account's
  radius having been much smaller earlier.
- With the radius unchanged, repeated searches returned the identical set
  (357 three times over 22 minutes).

**So the scraper sets that account radius itself.** Before searching, it
reads the radius Facebook is actually using and, if it isn't the target,
changes it through Marketplace's own "Change location" dialog, then reloads
and checks the change took effect. The target is the country's radius from
`COUNTRY_ANCHORS` — **500 km** for `ch`, the largest Facebook offers.

- `--radius KM` / `scrape(..., radius_km=KM)` picks a different radius —
  any positive number. Facebook only offers 1, 2, 5, 10, 20, 40, 60, 80,
  100, 250 and 500 km, so it's rounded up to the closest of those (30 → 40,
  101 → 250), or to 500 if bigger; the log says when it does.
- `--keep-account-radius` / `keep_account_radius=True` leaves your account
  alone and searches with whatever it's set to.
- This **changes your real Facebook account setting** — you'll see the new
  radius in your own browser too. Change it back any time under
  Marketplace → Location.
- If the radius can't be changed (e.g. Facebook changed the dialog), the
  scraper logs a warning and searches with the account's current radius
  instead of failing.

### Searching around another city

`--city NAME` / `scrape(..., city=NAME)` searches around another city. The
city goes into the search URL (which, unlike the radius, Facebook does
honour), so **your account's own location isn't changed**.

Facebook only accepts a few Swiss city names in the URL (`zurich`, `bern`,
`fribourg`, `zug`; not `geneva`, `basel`, `lausanne`, ...), but a numeric
Facebook location id works for every city. So the scraper looks the id up
the way the site does: it types the name into Marketplace's location field,
takes **the first place Facebook suggests inside the country** for the full
name, and closes the dialog without applying it. The log shows what it
picked, e.g. `city 'Altdorf' -> Altdorf, Uri (Facebook location ...)`.

- The pick can be a neighbourhood or nearby town rather than the city itself
  (`Genève` → Pregny, a Geneva suburb) — with a country-sized radius that
  makes no real difference.
- Places outside the country are skipped: Facebook's first suggestion for
  `Altdorf` is Altdorf in Bavaria; the scraper takes `Altdorf, Uri`.
- If nothing inside the country is suggested, it stops with an error listing
  what Facebook offered — try another spelling or add the region.
- A numeric Facebook location id (e.g. `110868505604715` for Geneva) is used
  as-is.

Tested with all 26 canton capitals plus Winterthur, Biel, Thun, Lugano,
Locarno, Davos and Zermatt (October 2026).

From Zurich, 250 km already reached 24 of the 26 cantons; 500 km found the
same "Tesla Model X" listings plus one from Germany. A big radius reaches
across the border; listings there are dropped by the country filter unless
you pass `--all-countries`.

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
| `--city` | City to search around, e.g. `Bern` or `Genève` (default: Zürich). See [Countries](#countries) |
| `--radius` | Search radius in km (default `500`), rounded up to one Facebook offers: 1, 2, 5, 10, 20, 40, 60, 80, 100, 250 or 500. Set on your Facebook account — see [Countries](#countries) |
| `--keep-account-radius` | Don't change your account's search radius |
| `--no-price-split` | Don't re-search big result sets (200+) in smaller price ranges (faster, fewer listings) |
| `--headed` | Show the browser (for first login or the consent screen) |
| `--email` / `--password` | Facebook login, or `FB_EMAIL`/`FB_PASSWORD` env vars |
| `--delay` | Seconds between detail-page visits (default `0.4`) |
| `--price-from` / `--price-to` | Filter by price, inclusive |
| `--condition` | Comma-separated: `new`, `used_like_new`, `used_good`, `used_fair` |
| `--version` | Print the installed version and exit |
| `-v` / `-q` | Verbose / quiet output |

### Examples

```bash
facebook-marketplace-scraper --query "Tesla Model S" --price-to 30000
facebook-marketplace-scraper --query "iPhone 15" --condition new,used_like_new
facebook-marketplace-scraper --query "MacBook Pro" --no-detail
```

### As a library

```python
from fb_scraper.scraper import scrape

result = scrape("Tesla Model S", max_price=30000)

result.rows  # list[dict]: one flattened dict per listing, CSV-ready
result.listings  # list[dict]: one dict per listing, see Data structure
result.query, result.country, result.total_elements

for row in result.rows:
    print(row["price"], row["condition"], row["url"])

result.to_csv("tesla_model_s.csv")  # optional — scrape() itself writes nothing
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
