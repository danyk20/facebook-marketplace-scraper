# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-03

Initial release.

### Added

- Scraper for Facebook Marketplace listings, usable both as a CLI
  (`facebook-marketplace-scraper` / `python main.py`) and as a library
  (`from fb_scraper.scraper import scrape`).
- Generic free-text search (`--query`) — not limited to vehicles, works for
  any item type Marketplace lists — with optional price/mileage/
  first-registration-year/condition filters, all mapped onto Facebook's
  own real search URL parameters (reverse-engineered by testing, not
  guessed).
- Drives a real Playwright/Chromium browser rather than an HTTP client:
  confirmed by testing that plain HTTP requests are blocked at what looks
  like a TLS/browser-fingerprint level, and that no separate public JSON
  API exists the way there is for the sibling AutoScout24Scraper project.
- Login support: credentials (`--email`/`--password`, `FB_EMAIL`/
  `FB_PASSWORD`, or a secure `-` prompt) fill and submit Facebook's own
  login form; `--headed` for manual login (handles 2FA); sessions persist
  across runs via a local browser profile. Distinguishes login failures,
  2FA/checkpoint challenges, and Facebook's separate EU/DMA Marketplace
  consent screen (deliberately not auto-accepted — a privacy choice left
  to a human) with dedicated, actionable exceptions.
- Full-detail mode (default): visits every matching listing individually
  to extract condition, full description, relative post date, and the
  full-size photo gallery; `--no-detail`/`detail=False` for a faster
  summary-only pass.
- `country` parameter (default `"ch"`, Switzerland) mapping to an anchor
  city + search radius rather than a hardcoded location, so another
  country can be added without scraper code changes; local-only filtering
  drops listings whose location doesn't look like it's actually inside
  the requested country.
- `ScrapeResult` dataclass return value (`.rows`, `.listings`, `.to_csv()`,
  `.to_json()`) for library use, matching AutoScout24Scraper's shape so
  the two scrapers can be used interchangeably; the CLI is a thin wrapper
  around the same `scrape()` function.
- Optional SQLite-backed helper (`fb_scraper/storage.py`) for tracking
  which listings are new since a previous run of the same search.
- Console script entry point (`facebook-marketplace-scraper`) and `pip
  install` support via `pyproject.toml` packaging metadata; `--version`
  flag.
- Logging-based output (`-v`/`--verbose`, `-q`/`--quiet`) instead of bare
  `print()`, so library consumers can configure/suppress it via the
  standard `logging` module.
- Full type hints throughout, checked with mypy; linted and formatted
  with Ruff.
- Unit test suite (96% coverage, all browser network traffic mocked via
  Playwright's `BrowserContext.route()`) plus a smaller end-to-end suite
  against the real live site.
- CI (GitHub Actions) running lint, type-check, and the unit suite
  (including a Chromium install step) on every push/PR.
- MIT license with an explicit statement welcoming AI agents/bots to use
  the project under the same terms as a human developer, and a
  non-affiliation disclaimer (this project has no relationship with Meta
  Platforms, Inc.).
- Project governance docs: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, issue/PR templates.
