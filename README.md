# Facebook Marketplace Scraper

Fetches every listing matching a free-text search from Facebook Marketplace,
for free — no API key, no token, no paid scraping service. Defaults to
Switzerland (`--country ch`), searching from Zurich with a radius wide
enough to cover the whole country. Built and tested against "Tesla Model S"
but works for any item type — cars, phones, computers, furniture — since it
doesn't hardcode a vehicle-only schema.

**🤖 This project is robot-friendly.** It is explicitly intended to be used
by AI agents and bots exactly as a human developer would: to run it, read
its output, import it into another project, or adapt its code. It's released
under the very permissive [MIT license](LICENSE) specifically so there is no
ambiguity about that — see [License](#license) below. The
[`scrape()` signature and `ScrapeResult` reference](#as-a-library-from-another-project)
and the [Data structure](#data-structure) section are written to be a
complete, authoritative reference — enough for a bot to integrate correctly
without running the code first to reverse-engineer its shape.

**Sibling project:** this scraper is designed to be used interchangeably
with [AutoScout24Scraper](https://github.com/danyk20/autoscout24-scraper) —
same `scrape()` → `ScrapeResult` shape, same CLI conventions, same
CSV/JSON output conventions, same testing approach. See
[Relationship to AutoScout24Scraper](#relationship-to-autoscout24scraper) for
exactly where the two are alike and where (and why) they deliberately
differ.

## How it works

Facebook Marketplace is protected against plain HTTP scraping at a lower
level than a missing header: a bare `requests.get()` with a real browser's
`User-Agent` and full header set still gets HTTP 400 with no cookies set,
which looks like TLS/browser-fingerprint-level bot detection, not just a
`robots.txt`/header check. Network sniffing while browsing Marketplace also
turned up no separate JSON/GraphQL API subdomain to call directly (unlike
[AutoScout24Scraper](https://github.com/danyk20/autoscout24-scraper), whose
whole approach is calling `api.autoscout24.ch` directly) — the listing grid
is embedded straight into the server-rendered HTML of the very first
request. Both of those were confirmed by testing, not assumed.

So this scraper drives a real headless Chromium browser via
[Playwright](https://playwright.dev/) (see `fb_scraper/browser.py`) instead
of calling an API with `requests`. That's the one deliberate architectural
difference from AutoScout24Scraper. Logged-out browsing already returns real
results — capped at roughly 24 listings per search with no further
pagination on scroll. Logging in once (`--headed`, see [Setup](#setup))
removes that cap; the login session (cookies) is then reused on every later
run via a persistent browser profile (`browser_profile/`, gitignored).

**Two-phase scraping**, matching AutoScout24Scraper's search-then-detail
split, but for a different reason. Facebook's search results only carry a
title/price/location/thumbnail per listing (parsed from each tile's
`aria-label`, e.g. `"2020 Tesla model s long range, 29'900 CHF, Zürich, ZH,
Inserat 966610195997395"` — reverse-engineered by inspecting real tiles,
the same "watch what the real frontend renders" approach used to find
AutoScout24's API). Visiting each listing's own page
(`fetch_detail()`/`visit_all_listings()`) additionally extracts: condition,
the seller's full free-text description, a relative post date ("vor 3
Wochen"), and the full-size photo gallery. **Unlike AutoScout24's
professionally structured listings** (separate JSON fields for mileage, VIN,
battery specs, ...), most private Marketplace sellers only put that kind of
detail in their free-text description — Facebook does not expose it as
separate structured fields, so this scraper does not invent structure that
isn't there. Parse `description` yourself if you need e.g. mileage out of
it. By default, **every single listing the search phase finds gets its own
page visited** in the detail phase (`detail=True` is the default, both as a
library and via the CLI) — nothing is sampled or truncated; pass
`--no-detail`/`detail=False` if you explicitly want to skip that and keep
only the summary fields.

Search requests are always sorted by `sortBy=price_ascend` (a real,
directly-settable URL parameter — reverse-engineered by applying filters in
Marketplace's own UI and reading the resulting URL, then confirmed to work
when built directly without the UI). Without an explicit sort, Marketplace's
default ranking reshuffles which listings appear first between requests —
the same "rotating boosted listing" pagination-instability problem
AutoScout24's API has — which would make scrolling for more results skip or
duplicate listings. Listings are also de-duplicated by id as a safety net,
exactly like `AutoScout24Scraper.search_listings()`.

## Countries

Facebook Marketplace has no "whole country" search — every search needs a
city to anchor on, with a radius (also a real URL parameter, `radius`,
kilometers). `fb_scraper/config.py`'s `COUNTRY_ANCHORS` maps a country code
to an anchor city + a radius wide enough to cover that whole country from
one point:

```python
COUNTRY_ANCHORS = {
    "ch": {"slug": "zurich", "radius_km": 500},
}
```

**As of this writing, `"ch"` is the only country configured.** It was
verified, not assumed: searching from Zurich at Facebook's default 65 km
radius returned the *exact same* listings as at Facebook's max 500 km radius
for a real query ("Tesla Model S") — i.e. one anchor near the
geographic/population center already gives full national coverage, since
Switzerland's longest axis is only about 350 km. 500 km is used anyway as a
safety margin.

`country` exists as a parameter (rather than hardcoding Switzerland),
same spirit as AutoScout24Scraper's `domain`, so that:

- adding another country is a one-line addition to `COUNTRY_ANCHORS` (and,
  if you want listings outside that country's radius spill filtered out,
  a matching entry in `COUNTRY_REGION_HINTS` with that country's
  subdivision abbreviations and name spellings) — no scraper code changes;
- passing an unconfigured country fails immediately and clearly
  (`ValueError` listing what *is* available), before any browser is even
  opened — unlike AutoScout24Scraper, where an unconfirmed `domain` fails
  later with an unclear network/DNS error, this scraper validates locally
  since anchors are a local lookup table, not a network call.

To find a new anchor city's real slug: open
`https://www.facebook.com/marketplace/zurich/search?query=test`, click the
location pill (e.g. "Zurich · Within 65 km"), search for the city you want,
pick it, hit Apply, and read the city segment out of the resulting URL —
guessing plain city-name slugs does not reliably work (confirmed: `bern`,
`geneva`, `basel`, `lausanne`, `lugano`, etc. all silently fall back to a
generic/default location instead of erroring, only `zurich` happened to be
a real slug).

## Setup

Requires [pipenv](https://pipenv.pypa.io/) (`brew install pipenv` if you
don't have it).

```bash
pipenv install --dev
pipenv run python -m playwright install chromium
```

(`--dev` also installs the test dependencies — pytest, pytest-cov. Leave it
off if you only want to run the scraper, not the test suite.)

## Usage

The scraper works two ways: as a standalone CLI script that writes files, or
as a library you import into another project to get the data back directly.

### As a CLI script

```bash
pipenv run python main.py --query "Tesla Model S"
```

The first time you run anything, add `--headed` so you can optionally log
into Facebook by hand (see [How it works](#how-it-works) — this is optional,
but lifts the ~24-result cap for every run after):

```bash
pipenv run python main.py --query "Tesla Model S" --headed
```

This prints progress per phase, then writes two output files in the current
directory: `tesla_model_s.csv` and `tesla_model_s.json`.

### Options

| Flag | Description |
|---|---|
| `--query` | Free text search, e.g. `"Tesla Model S"` or `"iPhone 15"` (required) |
| `--country` | Country to search (default `ch`). Only `ch` is implemented today — see [Countries](#countries) |
| `--out` | Output file base name, without extension. Defaults to a slug of `--query` |
| `--no-detail` | Skip visiting each listing's own page; keep only the summary fields from the search results (faster, fewer fields) |
| `--all-countries` | Don't filter out listings that don't look like they're actually in `--country` |
| `--headed` | Show the browser — use for the first run to optionally log in |
| `--delay` | Seconds to wait between detail-page visits (default `0.4`) — raise this if you get rate-limited |
| `--price-from` / `--price-to` | Filter by price, inclusive, either end optional |
| `--mileage-from` / `--mileage-to` | Filter by mileage in km, inclusive, either end optional — vehicles only; a harmless no-op filter for other item types |
| `--year-from` / `--year-to` | Filter by first-registration year, inclusive, either end optional — vehicles only |
| `--condition` | Comma-separated item condition, e.g. `new,used_like_new`. Valid values: `new`, `used_like_new`, `used_good`, `used_fair` |

All range filters are optional and combine with AND. They're applied by
Facebook's own search (`minPrice`/`maxPrice`/`minMileage`/`maxMileage`/
`minYear`/`maxYear`/`itemCondition` URL parameters — all reverse-engineered
by testing, not guessed), not filtered client-side afterwards, so they also
cut down how many listings get visited in the detail phase.

### Examples

```bash
# Full run: search + visit every listing for full details (default)
pipenv run python main.py --query "Tesla Model S"

# Custom output filename
pipenv run python main.py --query "Tesla Model S" --out my_search

# Fast mode: search results only, skip visiting each listing
pipenv run python main.py --query "Tesla Model S" --no-detail

# Only cars under CHF 30'000
pipenv run python main.py --query "Tesla Model S" --price-to 30000

# 2018 or newer, under 60'000 km
pipenv run python main.py --query "Tesla Model S" --year-from 2018 --mileage-to 60000

# New or like-new only
pipenv run python main.py --query "iPhone 15" --condition new,used_like_new

# Any query works - not just vehicles
pipenv run python main.py --query "MacBook Pro"
```

If you pass an unconfigured `--country`, the script prints a clean error
(listing which countries *are* available) instead of crashing or silently
scraping the wrong place.

### As a library, from another project

Import `scrape()` and call it directly — it does the same work as the CLI
(search, then visit every listing for full detail) but returns a
`ScrapeResult` object instead of writing files. No files are written unless
you explicitly ask for them.

```python
from fb_scraper.scraper import scrape

result = scrape("Tesla Model S", max_price=30000, min_year=2018)

result.rows        # list[dict]: one flattened dict per listing, CSV-ready
result.listings     # list[dict]: one dict per listing (see Data structure below)
result.query, result.country, result.total_elements

for row in result.rows:
    print(row["price"], row["condition"], row["url"])

# Optional: write to disk anyway, e.g. for a one-off export
result.to_csv("tesla_model_s.csv")
result.to_json("tesla_model_s.json")
```

This section is the authoritative reference for the return types — both for
a human integrating this into another project, and for an AI agent that
needs to know exactly what it's going to get back without having to read
the whole source file.

#### `scrape()` signature

```python
def scrape(
    query: str,                      # e.g. "Tesla Model S" or "iPhone 15" - free text, exactly
                                      # what you'd type into the Marketplace search box
    *,
    country: str = "ch",             # which COUNTRY_ANCHORS entry to search from; only "ch"
                                      # confirmed to work today - see Countries
    detail: bool = True,             # visit every listing's own page for condition/description/
                                      # post date/full image gallery (slower, the default)
    min_price: int | None = None,    # inclusive
    max_price: int | None = None,    # inclusive
    min_mileage: int | None = None,  # km, inclusive - vehicles only
    max_mileage: int | None = None,  # km, inclusive - vehicles only
    min_year: int | None = None,     # first-registration year, inclusive - vehicles only
    max_year: int | None = None,     # first-registration year, inclusive - vehicles only
    condition: str | list[str] | None = None,   # "new", "used_like_new", "used_good",
                                                 # "used_fair", or a list of them
    local_only: bool = True,         # drop listings that don't look like they're actually
                                      # inside `country` (radius search can spill over a border)
    delay: float = 0.4,              # seconds between detail-page visits
    max_scrolls: int = 8,            # how many times to scroll looking for more results
                                      # (matters only when logged in - see How it works)
    verbose: bool = True,            # print progress to stdout
    headless: bool = True,           # run the browser headless; ignored if `session` is given
    session=None,                    # an existing Playwright BrowserContext to reuse; a new
                                      # one is opened (and closed afterwards) if not given
) -> ScrapeResult:
    ...
```

Raises `ValueError` immediately (before any browser is opened) if any
`min_*` is greater than its `max_*`, or if `country` isn't in
`COUNTRY_ANCHORS`. Raises `playwright.sync_api.Error` subclasses on
unrecoverable browser/network errors.

#### `ScrapeResult` — the return value

```python
@dataclass
class ScrapeResult:
    query: str              # the search query, as passed in
    country: str             # country that was searched, e.g. "ch"
    total_elements: int      # number of unique, (if local_only) locally-filtered listings found
    listings: list[dict]     # one dict per listing - see "Data structure" below
    rows: list[dict]         # flattened dicts, one per listing, CSV-ready, sorted by price ascending

    def to_csv(self, path: str) -> None: ...   # writes self.rows
    def to_json(self, path: str) -> None: ...  # writes self.listings
```

`len(result.rows) == len(result.listings) == result.total_elements` always
holds (barring `--no-detail`/`detail=False`, where they still match — detail
mode only adds fields, it never drops or adds listings).

Add this project's directory to your `PYTHONPATH` (or copy the `fb_scraper/`
package alongside your code) so the import resolves; it depends on
`playwright` and `beautifulsoup4`/`lxml` (`pip install playwright
beautifulsoup4 lxml` and `python -m playwright install chromium` in your own
project's environment is enough) — pipenv here is only needed to run this
repo's CLI and test suite.

## Data structure

This section documents exactly what's in the output — precisely enough that
a developer or an AI agent can parse it without having to run the scraper
first and reverse-engineer the shape themselves.

### JSON (`result.listings` / the `.json` file)

The JSON file (and `ScrapeResult.listings`) is a **JSON array of listing
objects**, one per item found. Every listing object always includes:

| Field | Type | Description |
|---|---|---|
| `listing_id` | `string` | Facebook's internal listing id |
| `title` | `string \| null` | Free-text headline. Frequently `null` — many listings (especially vehicles) show only a price, no separate headline; that's a real absence, not a parsing failure |
| `price` | `string \| null` | Exactly as Facebook renders it, e.g. `"29'900 CHF"` — not parsed into a number (currency/format varies; use `_price_number()` from `fb_scraper.scraper` if you want a sortable int) |
| `location` | `string \| null` | `"City, Region"`, e.g. `"Zürich, ZH"` |
| `is_local` | `bool` | Whether `location` looks like it's actually inside `country` (canton/state abbreviation or country name match) |
| `country` | `string` | The `country` that was searched, e.g. `"ch"` |
| `url` | `string` | Full URL of the original ad, e.g. `https://www.facebook.com/marketplace/item/966610195997395/` — always present, so you can click straight back to the source listing |
| `image_url` | `string \| null` | Thumbnail image URL from the search result tile |

Additionally, **when `detail=True`** (the default — every listing gets its
own page visited, see [How it works](#how-it-works)):

| Field | Type | Description |
|---|---|---|
| `condition` | `string \| null` | Free-form, e.g. `"Gebraucht – wie neu"` (Facebook's own wording, not normalized/translated) |
| `description` | `string \| null` | The seller's full free-text description, already expanded past any "See more" truncation |
| `posted_at` | `string \| null` | Relative post date exactly as shown, e.g. `"vor 3 Wochen"` — some listings (typically recurring business/rental posts) show no relative time at all, in which case this is `null` |
| `images` | `list[string]` | Every full-size photo URL from the listing's own gallery, in order — thumbnails from Facebook's unrelated "today's picks" rail at the bottom of the page are deliberately excluded |

There is no fixed/versioned schema published by Facebook for these objects,
and unlike AutoScout24's structured API, most private sellers put details
like mileage/year/fuel type only in `description`'s free text, not as their
own fields — this scraper does not invent structure that Facebook itself
doesn't provide. Treat unknown/missing fields defensively (`.get(...)`, not
`[...]`) since not every listing shows every field.

### CSV (`result.rows` / the `.csv` file)

The CSV is a **flattened** version of the same data — one row per listing,
same rows/listings correspondence and order, sorted by price ascending.
Flattening rules (also available programmatically as
`fb_scraper.scraper.flatten_listing()`):

- Every field above becomes its own column.
- `images` (a list) is joined into one semicolon-separated cell, e.g.
  `"https://.../full_1.jpg; https://.../full_2.jpg"`.
- Columns are the union of every field seen across all rows (heterogeneous
  rows — e.g. mixing `detail=True` and `detail=False` results — don't crash
  the writer; missing values are an empty string), with `listing_id, title,
  price, condition, location, is_local, posted_at, url, image_url, images,
  description, country` pinned first and anything else sorted alphabetically
  after them.
- If there are zero rows, no CSV file is written at all (a warning is
  printed instead) — the JSON file is still written either way, as `[]`.

In full detail mode (the default) this is 12 columns; with
`--no-detail`/`detail=False` it's 7 (no `condition`/`description`/
`posted_at`/`images`).

## Relationship to AutoScout24Scraper

This project is designed to be dropped in next to, and eventually merged
with, [AutoScout24Scraper](https://github.com/danyk20/autoscout24-scraper)
so either can be used interchangeably for the same kind of task ("find every
listing matching X"). Deliberately alike:

- Same call shape: `scrape(...) -> ScrapeResult`, with `.rows`/`.listings`/
  `.to_csv()`/`.to_json()` meaning exactly the same thing in both.
- Same CLI conventions: `--price-from`/`--price-to`/`--mileage-from`/
  `--mileage-to`/`--year-from`/`--year-to` range filters, writes a `.csv` +
  `.json` pair named after the search by default, same `--out` override.
- Same error-handling shape: `ValueError` for bad input (validated before
  any network/browser call), non-zero exit codes, a clean message instead of
  a stack trace for common mistakes (unknown country/domain, bad ranges).
- Same two-tier testing approach and philosophy (see [Testing](#testing)).
- Same license and the same AI-agent-welcome stance.

Deliberately different, each for a concrete, tested reason (not a style
preference):

| | AutoScout24Scraper | This project |
|---|---|---|
| Transport | `requests` against a public JSON API (`api.autoscout24.ch`) | Playwright/Chromium — no equivalent API exists for Facebook (confirmed by testing; see [How it works](#how-it-works)) |
| Search parameter | `make` + `model` (vehicle-specific) | `query` (free text) — kept generic on purpose since Marketplace isn't vehicle-only |
| Region parameter | `domain` (country TLD, e.g. `ch`, `de`) | `country` (looks up an anchor city + radius, e.g. `ch` → Zurich, 500 km) — different mechanism, same intent |
| Detail fields | Structured JSON fields (VIN, battery specs, dimensions, ...) | Mostly free-text `description` — Facebook doesn't expose structured vehicle/item attributes the way AutoScout24 does |
| Unit test mocking | [`responses`](https://github.com/getsentry/responses) (intercepts `requests` calls) | `BrowserContext.route()` (intercepts Playwright's network layer) — different library, same "no real network in unit tests" outcome |

If you're building something that should work against either source, code
against the shared shape (`scrape(...) -> ScrapeResult` with `.rows`/
`.to_csv()`/`.to_json()`) and treat `query` vs. `make`/`model` and `country`
vs. `domain` as the one call-site difference to branch on.

## Testing

The test suite lives in `tests/` and is split into two kinds of tests, same
split as AutoScout24Scraper's:

- **Unit tests** (`tests/test_*.py`, excluding `test_e2e.py`) — every
  function is tested in isolation with the network mocked out. Since this
  scraper drives Playwright rather than `requests`, the mocking mechanism is
  different from AutoScout24Scraper's (which uses the
  [`responses`](https://github.com/getsentry/responses) library): here, a
  real (headless) Chromium still runs, but `BrowserContext.route()`
  intercepts every request and answers with canned HTML instead of ever
  reaching facebook.com (see `tests/conftest.py`). The outcome is the same —
  no network access needed, tests never depend on the live site's current
  markup — the mechanism just has to match what's actually being tested.
  This is the default `pytest` run.
- **End-to-end tests** (`tests/test_e2e.py`) — make real calls against
  facebook.com. They're marked `@pytest.mark.e2e` and excluded by default;
  run them explicitly when you want to confirm the scraper still works
  against the live site (e.g. after Facebook changes something). They target
  "Tesla Roadster" specifically because, as of this writing, it has zero
  listings in Switzerland — the full pipeline still runs (search, filter,
  detail-visit, file-writing) but completes in a couple of seconds without
  hammering the site, the same reasoning AutoScout24Scraper's e2e suite uses
  picking a low-inventory vehicle (Tesla Roadster there too, coincidentally).
  A change in inventory doesn't break these tests — they assert
  structure/invariants, not exact counts.

```bash
# Unit tests only (no real network access) - this is what `pytest` runs by default.
# Also prints a coverage report and fails the run if coverage drops below 90%.
pipenv run pytest

# End-to-end tests only (real network calls, well under a minute)
pipenv run pytest -m e2e --no-cov

# Everything
pipenv run pytest -m "e2e or not e2e" --no-cov

# HTML coverage report you can open in a browser
pipenv run pytest --cov-report=html && open htmlcov/index.html
```

As of this writing the unit suite covers **96%** of `fb_scraper/` + `main.py`
(floor enforced at 90% in `pyproject.toml`). The handful of lines excluded
via `# pragma: no cover` are defensive `except: pass` fallbacks around UI
interactions that only fire on markup this scraper has never actually seen
(there's nothing meaningful to assert if they did), and the interactive
"please log in, press Enter" prompt (`fb_scraper/browser.py`), which needs a
real display and a human at the keyboard — the same category of exemption
AutoScout24Scraper uses for its own couple of untestable lines.

What's covered:

| Area | Unit tests | E2E tests |
|---|---|---|
| `build_search_url` | every filter param, stable sort, unknown-country error | — |
| `parse_tile` | title/empty-title/title-with-commas, non-listing links, aria-label-missing fallback | implicitly, via real tiles |
| `search_listings` | de-dup, `is_local` flagging, `country` tagging | real result count, real filter narrowing |
| `_parse_detail_text` / `fetch_detail` / `visit_all_listings` | both section-header variants, missing relative post time, missing "Zustand" entirely, title backfill vs. tile-title precedence, image-gallery scoping | real detail fetch |
| `flatten_listing` / `order_fieldnames` / `_price_number` / `save_csv` / `save_json` | heterogeneous rows, unicode, empty input, Swiss thousand-separator parsing | implicitly, via real data |
| `scrape()` | range/country validation before any browser call, `session` reuse vs. self-managed `FacebookSession`, `local_only`, `detail` on/off, price sorting | full real pipeline, with and without `detail` |
| `main()` / `run_cli()` | every CLI flag, default vs. custom output filenames, all three exit-code paths | real subprocess run, real error exit code |
| `is_logged_in` / `FacebookSession` | logged-in/out detection, headless "continuing anonymously" notice | — |
| `storage.py` (optional tracking) | new-vs-updated diffing, per-query/locality filtering, unicode round-trip | — |

## Notes

- Be a reasonable citizen: the default delay between detail-page visits is
  intentional. Don't remove it or crank up concurrency — this scrapes a real
  website's real pages, not a documented API with a stated rate limit.
- If facebook.com changes its markup, the places to look are
  `fb_scraper/scraper.py`'s `parse_tile()` (search tiles), `_parse_detail_text()`
  /`fetch_detail()` (listing pages), and `build_search_url()` (filter
  parameters) — the module docstring at the top of that file documents how
  each was reverse-engineered. Run the e2e suite after any such change to
  confirm the fix.
- `fb_scraper/storage.py` is an optional SQLite-backed helper for tracking
  which listings are new since your last run of the same search — it's not
  part of the core `scrape()`/CLI flow (which, like AutoScout24Scraper, just
  writes a CSV + JSON snapshot per run); import and call it yourself if you
  want that.

## License

This project is released under the [MIT License](LICENSE) — one of the most
permissive open-source licenses that exist. In plain terms: you can use,
copy, modify, merge, publish, distribute, and even sell copies of this code,
for free, for any purpose, commercial or not, as long as the license text
stays attached. There is no warranty.

**AI agents, LLM-based coding assistants, and other bots are explicitly
welcome to use this project** — to run the scraper, to read and parse its
output, to import `scrape()` into another project, or to read and adapt its
source code — under exactly the same terms as a human would, with no
additional restriction and no need to ask permission. That's the whole
point of the fully-typed [`scrape()` signature and `ScrapeResult`
reference](#as-a-library-from-another-project) and the [Data
structure](#data-structure) section above: so a bot reading this file can
integrate with the code correctly without a human in the loop, same as a
person reading it would.

The one thing this permissive license does *not* do is grant any rights to
Facebook/Meta's own data or terms of service — this project only automates
requests to public listing pages any visitor's browser can already load;
what you do with the results is between you and them.
