"""
Facebook Marketplace scraper core.

Unlike AutoScout24 (a separate, unauthenticated JSON API subdomain,
api.autoscout24.ch), Facebook has no such API: plain HTTP requests are
blocked before any application logic runs (HTTP 400, no cookies set - this
looks like TLS/browser-fingerprint level bot detection, confirmed by
testing), and there's no public API either - the first batch of search
results is embedded as JSON in the server-rendered HTML, later batches
arrive from Facebook's internal /api/graphql/ endpoint while scrolling
(search_listings() reads both from inside the browser rather than calling
that endpoint itself). So this scraper drives a real Playwright/Chromium
browser (see browser.py) instead of calling an API with `requests`. That is the one deliberate, tested architectural
difference from AutoScout24Scraper; everything else mirrors it on purpose:
generic field extraction, a two-phase search-then-detail pipeline, a
ScrapeResult dataclass, and a scrape() library entry point with the same
shape (query in, ScrapeResult out).

Discovered request shape (found by trying filters in Marketplace's own UI
and reading the resulting URL, the same "watch what the real frontend does"
technique that found AutoScout24's API):

  GET https://www.facebook.com/marketplace/{location}/search
      ?query=...              free text, required
      (no radius param: Facebook ignores it for logged-in searches and
       uses the radius saved on the account instead - see
       set_account_search_radius())
      &minPrice=/&maxPrice=   price range (any currency shown on the site)
      (no minYear/maxYear/minMileage/maxMileage: Facebook applies those only
       to listings posted with structured vehicle data and silently drops
       every other listing - see scrape())
      &itemCondition=a,b      comma-separated: new, used_like_new,
                               used_good, used_fair
      &sortBy=creation_time_descend   see below

Facebook Marketplace has no "whole country" search - every search needs a
city to anchor on, with a radius. The sort order decides *which* listings
Facebook returns, not just their order - confirmed by testing every sortBy
value against the same searches ("Tesla Model X", 64 listings in total):

  creation_time_descend (newest)  64  - same set on every run
  distance_ascend (nearest)       64  - but varied between runs on bigger searches
  (no sortBy, "suggested")        39
  price_ascend / price_descend     2
  best_match, creation_time_ascend, distance_descend, unknown values
                                   0 here (yet a full result set for "iPhone
                                     15") - i.e. unsupported, unpredictable

So this scraper always sorts newest first, and sorts the final rows by
price itself (see scrape()). Listings are also de-duplicated by id as a
safety net, exactly like AutoScout24Scraper's search_listings().

Even newest-first, Facebook ends a big search early while still reporting
it as complete: "iPhone 15" stopped at 326 listings, while the same search
split into two price ranges (<= 300, >= 301) found 400, including all 326.
Ranges that returned up to ~230 listings didn't grow when split further, so
search_all_listings() splits any search that comes back with
SPLIT_THRESHOLD or more listings into price ranges and merges the results.

Logged-out browsing used to return real results directly (capped at ~24 per
search, no further pagination on scroll) - confirmed working during initial
development. That has since changed: as of this writing,
`/marketplace/{anchor}/search` hard-redirects anonymous visitors straight to
`/login`, confirmed by testing against multiple completely fresh browser
profiles (not something specific to one flagged session/profile). The bare
`/marketplace/` root (no city, no search) still loads anonymously, but
ignores the query entirely and just shows a generic nearby feed - not a
usable substitute. So logging in once (`--headed`, see browser.py) is now
effectively required, not just an optional cap-lifter. `search_listings()`
and `fetch_detail()` both detect a redirect to `/login` and raise
LoginRequiredError with an actionable message rather than silently
returning zero results - if you hit that, run with `--headed` and log in;
the session is then reused (via the persistent `browser_profile/`) on every
later run.

Two-phase scraping, same idea as AutoScout24Scraper but different reason:
the search results grid only has a title/price/location/thumbnail per
listing. Visiting each listing's own page (fetch_detail()) gets the
condition, full seller description, relative post date, and full-size
image gallery. Unlike AutoScout24's structured API fields (mileage, VIN,
battery specs as their own JSON keys), most private Marketplace sellers
only put those details in the free-text description - Facebook does not
return them as separate structured fields the way AutoScout24 does, so
this scraper does not invent structure that isn't there; parse the
description yourself if you need e.g. mileage out of it.

This module can be used two ways, same as AutoScout24Scraper:

1. As a CLI (see fb_scraper/cli.py, exposed as the `facebook-marketplace-scraper`
   console script once pip-installed; `main.py` is a thin dev wrapper around it):
    facebook-marketplace-scraper --query "Tesla Model S"
    facebook-marketplace-scraper --query "iPhone 15" --no-detail
    facebook-marketplace-scraper --query "Tesla Model S" --price-to 30000

2. As a library:
    from fb_scraper.scraper import scrape
    result = scrape("Tesla Model S", price_to=30000)
    for row in result.rows:
        print(row["price"], row["url"])
"""

from __future__ import annotations

import csv
import json
import logging
import random
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.sync_api import BrowserContext, Page, Response
from playwright.sync_api import Error as PlaywrightError

from . import config
from .browser import dismiss_overlays

logger = logging.getLogger(__name__)

Listing = dict[str, Any]

ITEM_RE = re.compile(r"/marketplace/item/(\d+)")
PROFILE_RE = re.compile(r"/marketplace/profile/(\d+)")

# Facebook renders each search-result tile's full aria-label as one
# structured string: "<title (may be empty)>, <price>, <city>,
# <canton/region>, Inserat <id>" ("Inserat" = German "listing"; anchored on
# the trailing id rather than that word so a locale change doesn't break it).
#
# The price token's own format depends on the *logged-in account's* saved
# Facebook UI language (confirmed by testing - not the browser locale, not a
# ?locale= URL param, neither of which affect an authenticated session):
# German renders digit-first with a period thousands separator and the
# currency after, e.g. "16.900 CHF" or "50 CHF"; English renders the
# currency first with no space and a *comma* thousands separator, e.g.
# "CHF16,900" or "CHF420". That comma matters beyond just parsing the price
# itself - since the whole aria-label is comma-separated, an English price
# like "CHF16,900" has to be matched as one token or it misaligns every
# field after it (city/region/id). Both shapes are matched explicitly below
# rather than a generic "digits + symbol" pattern, precisely to keep that
# internal comma from being mistaken for a field separator.
#
# A listing whose price was lowered has one extra field right after the
# price - "CHF170, reduced from CHF300, Schlieren, ZH, listing ..." (English)
# or "170 CHF, reduziert von ursprünglich 300 CHF, Schlieren, ZH, Inserat
# ..." (German), confirmed by testing. Without accounting for it, every
# field after the price shifts one place ("reduced from CHF300" becomes the
# city, "Schlieren" the canton) and is_local() wrongly drops the listing.
# Matched as optional words followed by a price token, so it doesn't depend
# on the translated wording.
_PRICE_TOKEN = r"[0-9][0-9'.]*\s*[A-Za-z]{2,5}|[A-Za-z]{2,5}[0-9][0-9,]*"
ARIA_RE = re.compile(
    r"^(?P<title>.*),\s*"
    rf"(?P<price>{_PRICE_TOKEN}),\s*"
    rf"(?:[^,0-9]*?(?P<original_price>{_PRICE_TOKEN}),\s*)?"
    r"(?P<city>[^,]+),\s*"
    r"(?P<region>[^,]+),\s*"
    r"\D*(?P<id>\d+)$"
)

PRICE_DIGITS_RE = re.compile(r"\d+")

SORT_BY = "creation_time_descend"  # see module docstring for why

# Scrolling: stop after this many scrolls in a row with nothing new, each
# pause randomized around SCROLL_PAUSE_MS - see scroll_to_load().
# DEFAULT_MAX_SCROLLS is only a safety cap; a search normally ends sooner,
# on Facebook's own has_next_page: false ("iPhone 15": ~20 scrolls).
DEFAULT_MAX_SCROLLS = 80
SCROLL_PAUSE_MS = 1500
# How long to let a freshly loaded search page settle before reading it.
PAGE_SETTLE_MS = 2500
SCROLL_IDLE_LIMIT = 5

# A search returning at least this many listings may have been cut off by
# Facebook, so it's split into price ranges - see search_all_listings().
SPLIT_THRESHOLD = 200
MAX_SPLIT_DEPTH = 4


class LoginRequiredError(RuntimeError):
    """Raised when Facebook redirected to /login instead of serving the page
    we asked for. Not a parsing failure - `page.url` genuinely is a login
    page after the goto(), checked directly rather than inferred from zero
    results, so this can't be confused with "the search legitimately matched
    nothing"."""


class MarketplaceConsentRequiredError(RuntimeError):
    """Raised when a logged-in account hasn't yet accepted Facebook's
    EU/DMA (Digital Markets Act) Marketplace data-usage consent
    (/privacy/consent/?flow=fb_dma_marketplace) - a real, separate gate from
    login: an account can be fully logged in and still get every Marketplace
    page redirected here instead until a human completes it once, picking
    the personalised/personalized profile option (declining leaves every
    later request, including unattended --email/--password runs, redirected
    back here). This is a privacy/legal choice about how Facebook uses the
    account's data, not a mechanical login step, so this scraper
    deliberately does not click through it automatically - it's for a
    human to decide, once, via --headed. In other words: the account behind
    whatever credentials you pass has to have opened Marketplace at least
    once before and gone through this dialog - a brand new account cannot
    be onboarded purely via --email/--password."""


class CityNotFoundError(ValueError):
    """Raised when Facebook's location search suggests no place inside the
    country for the requested city - see lookup_city()."""


class LocationNotRecognizedError(RuntimeError):
    """Raised when Facebook didn't recognise the location in the search URL
    and redirected to a generic search instead - which it then centres on
    the *account's* saved location, not the one asked for."""


class SearchRadiusError(RuntimeError):
    """Raised when the account's Marketplace search radius couldn't be set
    to the requested value (or the change didn't stick) - see
    set_account_search_radius()."""


def _raise_if_blocked(page: Page, what: str) -> None:
    if "/login" in page.url or "/checkpoint" in page.url or "two_step_verification" in page.url:
        raise LoginRequiredError(
            f"Facebook redirected to a login page while trying to load {what}. "
            f"Anonymous access to this URL isn't working right now - run with "
            f"--headed (or headless=False) once to log in; the session is then "
            f"reused on every later run. See README -> How it works."
        )
    if "/privacy/consent" in page.url:
        raise MarketplaceConsentRequiredError(
            f"Facebook redirected to its Marketplace data-usage consent screen while "
            f"trying to load {what}. This account is logged in but hasn't accepted that "
            f"consent yet (pick the personalised/personalized profile option) - run once "
            f"with --headed and click through it by hand (a privacy choice this scraper "
            f"won't make for you); the session is then reused on every later run. "
            f"See README -> How it works."
        )


def _raise_if_location_not_recognized(page: Page, location: str) -> None:
    """Facebook keeps /marketplace/<location>/ in the URL only if it knows
    that location; an unknown one (e.g. the slugs "geneva" or "basel" -
    confirmed by testing) redirects to /marketplace/category/search/ and
    silently searches around the account's own saved location instead."""
    if f"/marketplace/{location}/" not in page.url:
        raise LocationNotRecognizedError(
            f"Facebook doesn't recognise the location {location!r} - it redirected to {page.url} and would "
            f"search around your account's own location instead. Use a city name with --city/city=, or a "
            f"numeric Facebook location id."
        )


def listing_url(listing_id: str | int) -> str:
    return f"https://www.facebook.com/marketplace/item/{listing_id}/"


def seller_profile_url(seller_id: str | int) -> str:
    return f"https://www.facebook.com/marketplace/profile/{seller_id}/"


def build_search_url(
    query: str,
    country: str = config.DEFAULT_COUNTRY,
    *,
    min_price: int | None = None,
    max_price: int | None = None,
    condition: str | list[str] | None = None,
    location: str | None = None,
) -> str:
    """The search URL. `location` is a Facebook location id from
    lookup_city() (or a city slug Facebook knows, like "zurich"); defaults
    to the country's anchor slug."""
    anchor = config.anchor_for(country)
    params: dict[str, Any] = {
        "query": query,
        "exact": "false",
        "sortBy": SORT_BY,
    }
    if min_price is not None:
        params["minPrice"] = min_price
    if max_price is not None:
        params["maxPrice"] = max_price
    if condition:
        params["itemCondition"] = ",".join(condition) if isinstance(condition, (list, tuple)) else condition
    return f"https://www.facebook.com/marketplace/{location or anchor['slug']}/search?{urlencode(params)}"


def scroll_to_load(
    page: Page,
    max_scrolls: int = DEFAULT_MAX_SCROLLS,
    pause_ms: int | None = None,
    *,
    is_done: Callable[[], bool] | None = None,
    progress: Callable[[], int] | None = None,
    idle_limit: int = SCROLL_IDLE_LIMIT,
) -> None:
    """Scroll to trigger Marketplace's lazy-loaded results until there's
    nothing more to load.

    Stops as soon as `is_done()` is true (search_listings() passes "Facebook
    said has_next_page: false" - the one reliable end signal), or after
    `idle_limit` scrolls in a row where neither the page height nor
    `progress()` (responses received so far) changed - the fallback for when
    no such signal arrives. Not stopping at the first quiet scroll matters:
    Facebook sometimes takes longer than one pause to answer, and even sends
    empty batches mid-way through results (confirmed by testing), which made
    an earlier stop-at-first-quiet-scroll version end big searches anywhere
    between 85 and 223 listings when 227 were available.

    Each scroll distance and pause is randomized a little, so the scrolling
    isn't a perfectly regular machine rhythm."""
    pause_ms = SCROLL_PAUSE_MS if pause_ms is None else pause_ms
    last_height = last_progress = None
    idle = 0
    for _ in range(max_scrolls):
        if is_done and is_done():
            return
        page.mouse.wheel(0, random.randint(3000, 5000))
        page.wait_for_timeout(int(pause_ms * random.uniform(0.8, 1.6)))
        height = page.evaluate("document.body.scrollHeight")
        current_progress = progress() if progress else None
        idle = idle + 1 if (height, current_progress) == (last_height, last_progress) else 0
        if idle >= idle_limit:
            return
        last_height, last_progress = height, current_progress


def parse_tile(anchor: Tag) -> Listing | None:
    """Parse one search-result <a href="/marketplace/item/..."> tag (a
    bs4 Tag) into a plain dict, or None if it's not actually a listing link."""
    href = str(anchor.get("href", ""))
    href_match = ITEM_RE.search(href)
    if not href_match:
        return None
    listing_id = href_match.group(1)

    aria_label = str(anchor.get("aria-label") or "")
    title = price = original_price = city = region = None
    m = ARIA_RE.match(aria_label)
    if m:
        title = m.group("title").strip() or None
        price = m.group("price").strip()
        original_price = m.group("original_price")
        city = m.group("city").strip()
        region = m.group("region").strip()

    location = f"{city}, {region}" if city and region else None

    if not m:
        # aria-label missing or in an unexpected shape: fall back to the
        # longest text span on the tile. An empty title from a *successful*
        # aria-label match is legitimate (many listings genuinely have no
        # free-text title, just a price) and must not trigger this fallback.
        texts = [s.get_text(strip=True) for s in anchor.find_all("span")]
        texts = [t for t in texts if t and t not in (price, location) and not (price and price in t)]
        title = max(texts, key=len) if texts else None

    img = anchor.find("img")
    image_url = img.get("src") if img else None

    return {
        "listing_id": listing_id,
        "title": title,
        "price": price,
        "original_price": original_price,
        "location": location,
        "url": listing_url(listing_id),
        "image_url": image_url,
    }


# --- Search results from Facebook's own JSON --------------------------------
#
# Reading the rendered tiles alone loses listings: the search grid is
# virtualized - tiles scrolled past are removed from the DOM again - so the
# tiles still present after scrolling to the end are only the last ~37 or
# so. Confirmed by testing ("Tesla Model X"): 64 listings arrived, only 37
# tiles were left on the page at the end. The data behind every tile arrives
# as JSON though, with real keys instead of a positional aria-label: the
# first batch embedded in the search page's own HTML (<script
# type="application/json">), every later batch as an /api/graphql/ response
# fetched while scrolling - both the same shape, under
# data.marketplace_search.feed_units.edges[].node.listing. So
# search_listings() listens to those responses as they arrive and only falls
# back to parse_tile() for a tile with no JSON behind it.

GRAPHQL_URL_PART = "/api/graphql"
_JSON_SCRIPT_RE = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)


def _json_docs_from_html(html: str) -> list[Any]:
    """Every embedded JSON document in a page that could hold search
    results (skipping the many unrelated ones, for speed)."""
    docs = []
    for block in _JSON_SCRIPT_RE.findall(html):
        if "marketplace_search" not in block:
            continue
        try:
            docs.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    return docs


def _json_docs_from_graphql(body: str) -> list[Any]:
    """Every JSON document in one /api/graphql/ response body - possibly
    prefixed with "for (;;);" and possibly several documents, one per line
    (streamed responses)."""
    docs = []
    for line in body.removeprefix("for (;;);").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            docs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return docs


def _search_feed_units(obj: Any) -> Iterator[dict[str, Any]]:
    """Every `marketplace_search.feed_units` object anywhere in `obj` - one
    batch of search results: `edges` (the listings) plus `page_info`."""
    if isinstance(obj, dict):
        search = obj.get("marketplace_search")
        if isinstance(search, dict) and isinstance(search.get("feed_units"), dict):
            yield search["feed_units"]
        for value in obj.values():
            yield from _search_feed_units(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _search_feed_units(value)


def _collect_listing_nodes(obj: Any, out: dict[str, dict[str, Any]]) -> None:
    """Find every listing object (a dict with both "id" and
    "listing_price") anywhere in `obj`, keyed by id - first one seen wins."""
    if isinstance(obj, dict):
        if "id" in obj and "listing_price" in obj:
            out.setdefault(str(obj["id"]), obj)
            return
        for value in obj.values():
            _collect_listing_nodes(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _collect_listing_nodes(value, out)


def _collect_search_listing_nodes(obj: Any, out: dict[str, dict[str, Any]]) -> None:
    """Like _collect_listing_nodes(), but only inside search-result batches -
    listings in other feeds/recommendations on the same page are ignored."""
    for feed in _search_feed_units(obj):
        _collect_listing_nodes(feed, out)


def listing_from_json(node: dict[str, Any]) -> Listing:
    """Turn one listing object from Facebook's search JSON into the same
    dict shape parse_tile() returns. Prices keep Facebook's own formatting
    (`formatted_amount`, e.g. "CHF16,900"), same as the tile's aria-label."""
    listing_id = str(node["id"])
    geo = (node.get("location") or {}).get("reverse_geocode") or {}
    city, region = geo.get("city"), geo.get("state")
    location: str | None
    if city and region:
        location = f"{city}, {region}"
    else:
        # e.g. "Zürich, Switzerland" - still recognised by config.is_local()
        location = (geo.get("city_page") or {}).get("display_name") or city
    image = (node.get("primary_listing_photo") or {}).get("image") or {}
    return {
        "listing_id": listing_id,
        "title": (node.get("marketplace_listing_title") or "").strip() or None,
        "price": (node.get("listing_price") or {}).get("formatted_amount"),
        "original_price": (node.get("strikethrough_price") or {}).get("formatted_amount"),
        "location": location,
        "url": listing_url(listing_id),
        "image_url": image.get("uri"),
    }


def search_listings(
    page: Page,
    query: str,
    country: str = config.DEFAULT_COUNTRY,
    *,
    min_price: int | None = None,
    max_price: int | None = None,
    condition: str | list[str] | None = None,
    location: str | None = None,
    max_scrolls: int = DEFAULT_MAX_SCROLLS,
    verbose: bool = True,
) -> list[Listing]:
    """Fetch every listing one search page returns for `query`,
    de-duplicated by id. See the module docstring for why it's always sorted
    newest first, and search_all_listings() for searches too big for one
    page."""
    url = build_search_url(
        query,
        country=country,
        min_price=min_price,
        max_price=max_price,
        condition=condition,
        location=location,
    )
    if verbose:
        logger.info("  %s", url)

    # Collected as each batch arrives (embedded batch, then every scroll
    # batch), so scroll_to_load() can stop as soon as Facebook says the
    # results have ended.
    nodes: dict[str, dict[str, Any]] = {}
    batches = 0
    has_next_page = True

    def absorb(docs: list[Any]) -> None:
        nonlocal batches, has_next_page
        for doc in docs:
            for feed in _search_feed_units(doc):
                batches += 1
                _collect_listing_nodes(feed, nodes)
                if (feed.get("page_info") or {}).get("has_next_page") is False:
                    has_next_page = False

    def on_response(response: Response) -> None:
        if GRAPHQL_URL_PART not in response.url:
            return
        try:
            body = response.text()
        except Exception:  # body no longer available (e.g. navigated away) - nothing to read
            logger.debug("could not read GraphQL response body from %s", response.url)
            return
        absorb(_json_docs_from_graphql(body))

    page.on("response", on_response)
    try:
        response = page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(PAGE_SETTLE_MS)
        _raise_if_blocked(page, "the search results")
        _raise_if_location_not_recognized(page, location or config.anchor_for(country)["slug"])
        absorb(_json_docs_from_html(response.text() if response is not None else ""))
        dismiss_overlays(page)
        scroll_to_load(page, max_scrolls=max_scrolls, is_done=lambda: not has_next_page, progress=lambda: batches)
        rendered = page.content()
    finally:
        page.remove_listener("response", on_response)

    # JSON first, then any rendered tile that had no JSON behind it.
    found: dict[str, Listing] = {listing_id: listing_from_json(node) for listing_id, node in nodes.items()}

    tiles_only = 0
    for a in BeautifulSoup(rendered, "lxml").find_all("a", href=ITEM_RE):
        item = parse_tile(a)
        if item and item["listing_id"] not in found:
            found[item["listing_id"]] = item
            tiles_only += 1

    listings = list(found.values())
    for item in listings:
        item["country"] = country
        item["is_local"] = config.is_local(item.get("location"), country)

    if verbose:
        logger.info("  found %d unique listings", len(listings))
    logger.debug("  %d from Facebook's JSON, %d from rendered tiles only", len(nodes), tiles_only)
    return listings


# --- Search radius (an account setting, not a URL parameter) ----------------
#
# For a logged-in session Facebook ignores any `radius` URL param and uses
# the radius saved on the account - the "<city> · Within N km" location
# filter on every search page, changed via its "Change location" dialog.
# Confirmed by testing (October 2026): radius=65/150/500 in the URL all
# returned the same 357 "Tesla Model X" listings, Geneva included, because
# the account was set to 250 km; the page's own data said so
# ("filter_radius_km": 250). The same setting is what you see in your normal
# browser, so changing it here changes it there too.

# The only radii the "Change location" dialog offers, in km (confirmed by
# testing).
ALLOWED_RADII_KM = (1, 2, 5, 10, 20, 40, 60, 80, 100, 250, 500)


def supported_radius_km(radius_km: float) -> int:
    """The radius Facebook can actually use for `radius_km`: the closest
    value in ALLOWED_RADII_KM that's at least as big (30 -> 40, 101 -> 250),
    or the maximum for anything bigger (600 -> 500). Raises ValueError for
    zero or a negative radius."""
    if radius_km <= 0:
        raise ValueError(f"radius_km must be greater than 0, got {radius_km!r}")
    return next((r for r in ALLOWED_RADII_KM if r >= radius_km), ALLOWED_RADII_KM[-1])


_RADIUS_DATA_RE = re.compile(r'"filter_radius_km"\s*:\s*([0-9.]+)')
# The location filter button's aria-label ends in the radius, e.g. English
# "Location: Zürich, Switzerland, Within 250 km" - matched on the "<n> km"
# part only, which reads the same in German/French.
_LOCATION_BUTTON_RE = re.compile(r"\b\d+\s*km\b", re.I)
_APPLY_LABELS = ("Apply", "Übernehmen", "Anwenden", "Appliquer", "Applica")
_RADIUS_DIALOG = '[role="dialog"]:has([role="combobox"][aria-haspopup="listbox"])'
_UI_TIMEOUT_MS = 10_000


def account_search_radius(page: Page) -> int | None:
    """The radius (km) Facebook actually applies to the search page that's
    currently loaded, read from the page's own data - or None if the page
    doesn't say (e.g. not a search page, or Facebook changed its markup)."""
    match = _RADIUS_DATA_RE.search(page.content())
    return int(float(match.group(1))) if match else None


def set_account_search_radius(
    page: Page,
    radius_km: float,
    query: str,
    country: str = config.DEFAULT_COUNTRY,
    verbose: bool = True,
    location: str | None = None,
) -> None:
    """Make sure the account's saved Marketplace search radius is
    `radius_km`, changing it through Marketplace's own "Change location"
    dialog if it isn't, then reloading and checking it took effect.

    This changes a setting on the Facebook account itself (the same one the
    user sees in their own browser) - which is the point: it's the only
    radius Facebook honours. `radius_km` is rounded up to a radius Facebook
    offers first - see supported_radius_km(). Raises ValueError for zero or
    a negative radius, and SearchRadiusError if the account's current radius
    can't be read, if the dialog doesn't offer that radius, or if the change
    didn't stick."""
    radius_km = supported_radius_km(radius_km)
    url = build_search_url(query, country, location=location)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(PAGE_SETTLE_MS)
    _raise_if_blocked(page, "the search page")
    current = account_search_radius(page)
    if current is None:
        raise SearchRadiusError("couldn't read the account's current search radius from the search page")
    if current == radius_km:
        if verbose:
            logger.info("  search radius: %d km (account setting)", current)
        return

    dismiss_overlays(page)
    page.get_by_role("button", name=_LOCATION_BUTTON_RE).first.click(timeout=_UI_TIMEOUT_MS)
    dialog = page.locator(_RADIUS_DIALOG).last
    dialog.locator('[role="combobox"][aria-haspopup="listbox"]').first.click(timeout=_UI_TIMEOUT_MS)
    page.locator('[role="option"]').first.wait_for(timeout=_UI_TIMEOUT_MS)
    options = page.locator('[role="option"]')
    offered: dict[int, Any] = {}
    for i in range(options.count()):
        number = re.match(r"\s*(\d+)", options.nth(i).inner_text())
        if number:
            offered[int(number.group(1))] = options.nth(i)
    if radius_km not in offered:
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        raise SearchRadiusError(f"the location dialog doesn't offer {radius_km} km (it offers {sorted(offered)})")
    offered[radius_km].click(timeout=_UI_TIMEOUT_MS)

    apply = next(
        (b for b in (dialog.get_by_role("button", name=label, exact=True) for label in _APPLY_LABELS) if b.count()),
        dialog.locator('[role="button"]').last,  # unknown UI language: Apply is the dialog's last button
    )
    apply.click(timeout=_UI_TIMEOUT_MS)
    page.wait_for_timeout(PAGE_SETTLE_MS)

    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(PAGE_SETTLE_MS)
    now = account_search_radius(page)
    if now != radius_km:
        raise SearchRadiusError(f"tried to change the search radius from {current} to {radius_km} km, but it's {now}")
    if verbose:
        logger.info("  search radius: changed the account setting from %d to %d km", current, radius_km)


# --- City (a URL path segment, looked up by name) -----------------------------
#
# Unlike the radius, the location in the search URL *is* honoured:
# /marketplace/<location>/search searches around <location>. Only a few
# Swiss city slugs work there ("zurich", "bern", "fribourg", "zug" did;
# "geneva", "basel", "lausanne", "lugano", ... redirect away), but a numeric
# Facebook location id works for every city tested. lookup_city() gets that
# id the way the site itself does: by typing the name into the "Change
# location" dialog's location field and reading the suggestions Facebook
# sends back (`city_street_search`), each with an id and coordinates - then
# closing the dialog *without* applying, so the account's own location is
# never changed.
#
# Facebook sends suggestions for every partial text while typing ("G",
# "Ge", "Gen" ...), and those can put e.g. Berlin first - so only the
# suggestions for the complete text are used. Of those, the first one
# inside the country's bounds wins: it may be a neighbourhood or a nearby
# town rather than the city itself ("Genève" -> Pregny, a Geneva suburb),
# which makes no real difference with a country-sized radius, while the
# bounds check stops e.g. "Altdorf" resolving to Altdorf in Bavaria.


def _strings_in(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _strings_in(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _strings_in(value)


def _request_texts(post_data: str | None) -> set[str]:
    """Every string value in a GraphQL request's form-encoded body,
    including those inside its JSON `variables` - i.e. what it searched for."""
    texts: set[str] = set()
    for values in parse_qs(post_data or "").values():
        for value in values:
            texts.add(value)
            try:
                texts.update(_strings_in(json.loads(value)))
            except json.JSONDecodeError:
                pass
    return texts


def _location_suggestions(doc: Any) -> list[dict[str, Any]]:
    """The location suggestions in one city_street_search response, in
    Facebook's order: name, id and coordinates."""
    suggestions = []
    edges = ((((doc or {}).get("data") or {}).get("city_street_search") or {}).get("street_results") or {}).get("edges")
    for edge in edges or []:
        node = (edge or {}).get("node") or {}
        page_id = (node.get("page") or {}).get("id")
        location = node.get("location") or {}
        if page_id and location.get("latitude") is not None and location.get("longitude") is not None:
            suggestions.append(
                {
                    "name": node.get("single_line_address"),
                    "id": str(page_id),
                    "lat": float(location["latitude"]),
                    "lon": float(location["longitude"]),
                }
            )
    return suggestions


def lookup_city(page: Page, city: str, country: str = config.DEFAULT_COUNTRY) -> tuple[str, str]:
    """Find the Facebook location id for `city`: the first place Facebook's
    own location search suggests for the full name that lies inside
    `country`'s bounds. Returns (location_id, suggested_name). Doesn't change
    anything on the account. Raises CityNotFoundError if no suggestion is
    inside the country."""
    lat_min, lat_max, lon_min, lon_max = config.anchor_for(country)["bounds"]
    responses: list[tuple[set[str], list[dict[str, Any]]]] = []

    def on_response(response: Response) -> None:
        if GRAPHQL_URL_PART not in response.url:
            return
        try:
            body = response.text()
        except Exception:  # body no longer available - nothing to read
            return
        if "city_street_search" not in body:
            return
        suggestions = [s for doc in _json_docs_from_graphql(body) for s in _location_suggestions(doc)]
        responses.append((_request_texts(response.request.post_data), suggestions))

    page.goto(build_search_url("", country), wait_until="domcontentloaded")
    page.wait_for_timeout(PAGE_SETTLE_MS)
    _raise_if_blocked(page, "the location search")
    dismiss_overlays(page)
    page.on("response", on_response)
    try:
        page.get_by_role("button", name=_LOCATION_BUTTON_RE).first.click(timeout=_UI_TIMEOUT_MS)
        field = page.locator(_RADIUS_DIALOG).last.locator('input[role="combobox"]').first
        field.click(timeout=_UI_TIMEOUT_MS)
        field.fill("")
        field.press_sequentially(city, delay=60)
        # wait for the suggestions for the complete text, not a partial one
        for _ in range(_UI_TIMEOUT_MS // 250):
            if any(city in texts for texts, _ in responses):
                break
            page.wait_for_timeout(250)
    finally:
        page.remove_listener("response", on_response)
        page.keyboard.press("Escape")  # close the dialog WITHOUT applying - the account's location stays as is
        page.keyboard.press("Escape")

    full = [suggestions for texts, suggestions in responses if city in texts]
    suggestions = full[-1] if full else (responses[-1][1] if responses else [])
    for s in suggestions:
        if lat_min <= s["lat"] <= lat_max and lon_min <= s["lon"] <= lon_max:
            return s["id"], s["name"]
    offered = [s["name"] for s in suggestions]
    raise CityNotFoundError(
        f"Facebook's location search suggests nothing inside {country!r} for {city!r}"
        + (f" (it suggested: {', '.join(map(str, offered))})" if offered else "")
        + " - try another spelling, or add the region, e.g. 'Altdorf, Uri'."
    )


def _price_split_point(listings: list[Listing], min_price: int | None, max_price: int | None) -> int | None:
    """The median price of `listings`, as a whole number to split the
    [min_price, max_price] range at - or None if that wouldn't make either
    half smaller (no prices, or they're all at the top of the range)."""
    prices = sorted(p for p in (_price_number(item.get("price")) for item in listings) if p is not None)
    if not prices:
        return None
    split = prices[len(prices) // 2]
    if split < (min_price or 0) or (max_price is not None and split >= max_price):
        return None
    return split


def search_all_listings(
    page: Page,
    query: str,
    country: str = config.DEFAULT_COUNTRY,
    *,
    min_price: int | None = None,
    max_price: int | None = None,
    split_threshold: int | None = SPLIT_THRESHOLD,
    verbose: bool = True,
    _depth: int = 0,
    **search_kwargs: Any,
) -> list[Listing]:
    """search_listings(), but a search returning `split_threshold` or more
    listings - one Facebook may have cut off early (see module docstring) -
    is searched again as two price ranges, split at the median price found
    ([min, median] and [median + 1, max]; Facebook's price filters are
    inclusive, confirmed by testing), recursively up to MAX_SPLIT_DEPTH
    levels. All results are merged and de-duplicated by id. Pass
    `split_threshold=None` to never split."""
    listings = search_listings(
        page, query, country, min_price=min_price, max_price=max_price, verbose=verbose, **search_kwargs
    )
    if split_threshold is None or len(listings) < split_threshold or _depth >= MAX_SPLIT_DEPTH:
        return listings
    split = _price_split_point(listings, min_price, max_price)
    if split is None:
        return listings
    if verbose:
        logger.info("  %d listings - searching again in two price ranges, split at %d", len(listings), split)
    found = {item["listing_id"]: item for item in listings}
    for lo, hi in ((min_price, split), (split + 1, max_price)):
        for item in search_all_listings(
            page,
            query,
            country,
            min_price=lo,
            max_price=hi,
            split_threshold=split_threshold,
            verbose=verbose,
            _depth=_depth + 1,
            **search_kwargs,
        ):
            found.setdefault(item["listing_id"], item)
    if verbose and _depth == 0:
        logger.info("  found %d unique listings in total", len(found))
    return list(found.values())


# --- Structural (language-independent) detail extraction ------------------
#
# The previous approach here matched literal words ("Zustand"/"Condition",
# "Gepostet ... hier: ..."/"Listed ... ago in ...") - which meant it only
# ever worked for whichever languages someone had explicitly tested against.
# Confirmed by testing (switching the same real account between English,
# German and French and re-reading the same real listings): the *rendered
# text* changes completely per language, but the *DOM shape* Facebook uses
# to lay the page out does not. Reading that shape instead of the words in
# it is what makes this work for a language that was never specifically
# tested - it doesn't need to recognize the language, only the layout:
#
#   <h1>title</h1>
#   ...
#   <abbr aria-label="X weeks ago">X weeks ago</abbr>   (optional - see below)
#   ...
#   <h2>description-section header</h2>                (wording varies: seen
#       [condition label]                                "Beschreibung durch
#       [condition value]                                 den Verkäufer",
#       description text (one or more leaf nodes)         "Details", "Seller's
#       [translate/see-more toggle - <a>/<div role=button>] description",
#       location                                           "Description")
#       "location is approximate" caption
#   <h2>seller-info header</h2>
#   ...
#   <h2>related-listings header</h2>
#
# The header *wording* differs per language and per listing type (private
# vs. business vs. rental) - "Details" for a private listing means something
# different than "Details" would elsewhere - but its *position* doesn't: the
# description-section header is always exactly two h2 elements before the
# "related listings" header, and the seller-info header is always exactly
# one before it - confirmed across normal, business and rental listings by
# testing, since rental listings insert an *extra* h2 ("Property for rent
# location") before the description header, which counting from the front
# (h2[0]) would have got wrong but counting from the back does not.
#
# Within the description-section range, leaf nodes whose immediate DOM
# parent is a <span> are short labelled fields (condition, location, the
# "location is approximate" caption); the one whose immediate parent is a
# <div> is the actual free-text description - a real structural difference
# in how Facebook lays these out, not a translation of anything. Elements
# under a role="button" ancestor (the "see more"/"see translation" toggles)
# are excluded outright since they're UI chrome, not content, in any
# language.
#
# Tested end-to-end against real listings with the same real account set to
# English, German and French (including a listing with no condition set,
# and a rental listing whose h2 order differs from a normal listing's) -
# not tested against any other language, but nothing in the extraction
# depends on which language it is, only on this layout, so it should hold
# for any language Facebook renders Marketplace in.
_DETAIL_STRUCTURE_JS = """
() => {
    const main = document.querySelector('div[role="main"]');
    if (!main) return null;

    const h1 = main.querySelector('h1');
    const title = h1 ? h1.innerText.trim() || null : null;

    const abbr = main.querySelector('abbr');
    const postedAt = abbr ? (abbr.getAttribute('aria-label') || abbr.innerText || '').trim() || null : null;

    const h2s = Array.from(main.querySelectorAll('h2'));
    if (h2s.length < 2) {
        return {title, postedAt, condition: null, description: null, location: null, matched: false};
    }
    const descHeader = h2s.length >= 3 ? h2s[h2s.length - 3] : h2s[0];
    const sellerHeader = h2s[h2s.length - 2];

    const isButtonAncestor = (el) => {
        let anc = el;
        for (let i = 0; i < 4 && anc; i++) {
            if (anc.getAttribute && anc.getAttribute('role') === 'button') return true;
            anc = anc.parentElement;
        }
        return false;
    };

    // A "leaf" here means the deepest element actually holding text, not
    // strictly a childless one: a multi-line description can be one <span>
    // with <br> tags between lines rather than a single text node with
    // embedded newlines (confirmed by testing on a longer real
    // description) - such a span has non-zero children.length but none of
    // those children (the <br>s) carry any text of their own.
    const isTextLeaf = (el) => {
        for (const child of el.children) {
            if (child.innerText && child.innerText.trim()) return false;
        }
        return true;
    };

    const all = Array.from(main.querySelectorAll('*'));
    const leaves = all
        .filter((el) => {
            if (!isTextLeaf(el)) return false;
            if (!el.innerText || !el.innerText.trim()) return false;
            if (descHeader.contains(el) || el === descHeader) return false;
            if (sellerHeader.contains(el) || el === sellerHeader) return false;
            const afterHeader = !!(descHeader.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
            const beforeSeller = !!(sellerHeader.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING);
            if (!afterHeader || !beforeSeller) return false;
            return !isButtonAncestor(el);
        })
        .map((el) => ({text: el.innerText.trim(), parentTag: el.parentElement ? el.parentElement.tagName : null}));

    let rest = leaves;
    let location = null;
    if (rest.length >= 2 && rest[rest.length - 1].parentTag === 'SPAN' && rest[rest.length - 2].parentTag === 'SPAN') {
        location = rest[rest.length - 2].text;
        rest = rest.slice(0, -2);
    }

    let condition = null;
    if (rest.length >= 2 && rest[0].parentTag === 'SPAN' && rest[1].parentTag === 'SPAN') {
        condition = rest[1].text;
        rest = rest.slice(2);
    }

    const description = rest.map((r) => r.text).join('\\n').trim() || null;
    return {title, postedAt, condition, description, location, matched: true};
}
"""


def _extract_detail_structural(page: Page) -> dict[str, Any]:
    """Language-independent extraction via DOM shape - see the big comment
    above _DETAIL_STRUCTURE_JS. Returns `matched: False` when the page
    doesn't have the expected h1/>=2 h2 layout at all (e.g. a genuinely
    different page, not just a different language) so callers can fall back
    to _parse_detail_text instead of trusting empty structural results."""
    try:
        result = page.evaluate(_DETAIL_STRUCTURE_JS)
    except Exception:
        result = None
    if not result:
        return {
            "matched": False,
            "title": None,
            "postedAt": None,
            "condition": None,
            "description": None,
            "location": None,
        }
    return result


# --- Legacy text-matching fallback ------------------------------------------
#
# Kept as a fallback for _extract_detail_structural() above: if a listing's
# page doesn't have the expected h1/h2 layout (matched=False) - some other
# language/layout not covered by the structural approach - this still
# recovers *something* for the two languages it was originally built and
# tested against, rather than returning nothing at all.
#
# Also used as the title fallback when the structural extraction's <h1>
# lookup comes back empty: German is "<item> – Facebook Marketplace |
# Facebook" (suffix), English is "Marketplace – <item> | Facebook" (prefix).
_TITLE_SUFFIX_RE = re.compile(r"\s*[–-]\s*.*Facebook Marketplace.*$")
_TITLE_PREFIX_RE = re.compile(r"^Marketplace\s*[–-]\s*")
_TITLE_TRAILING_FACEBOOK_RE = re.compile(r"\s*\|\s*Facebook\s*$")

# German: "Gepostet vor 3 Wochen – hier: Zürich, ZH"
# English: "Listed 23 weeks ago in Andwil, SG"
_POSTED_PATTERNS = (
    re.compile(r"Gepostet\s*(?P<posted_at>[^–\n-]*?)\s*[–-]\s*hier:\s*(?P<location>[^\n]+)"),
    re.compile(r"Listed\s*(?P<posted_at>.*?)\s*\bin\b\s*(?P<location>[^\n]+)"),
)
_DESCRIPTION_HEADERS = ("Beschreibung durch den Verkäufer", "Details", "Seller's description", "Description")
_CONDITION_LABELS = ("Zustand", "Condition")
_DESCRIPTION_STOP_MARKERS = (
    "Mehr ansehen",
    "See more",
    "See translation",
    "Nachricht senden",
    "Message",
    "Message Seller",
    "Heutige Auswahl",
    "Today's picks",
    "Location is approximate",
)


def _parse_detail_text(text: str) -> dict[str, str | None]:
    lines = [ln.strip() for ln in text.split("\n")]

    header_index = None
    for i, ln in enumerate(lines):
        if ln in _DESCRIPTION_HEADERS:
            header_index = i
            break

    condition = None
    body_start = header_index + 1 if header_index is not None else None
    if body_start is not None and body_start < len(lines) and lines[body_start] in _CONDITION_LABELS:
        if body_start + 1 < len(lines):
            condition = lines[body_start + 1].strip() or None
        body_start += 2

    if condition is None:
        # Fallback for layouts where the condition line appears without a
        # recognized header nearby - still worth capturing on its own.
        for i, ln in enumerate(lines):
            if ln in _CONDITION_LABELS and i + 1 < len(lines):
                condition = lines[i + 1].strip() or None
                if body_start is None:
                    body_start = i + 2
                break

    description = None
    if body_start is not None:
        desc_lines = []
        for ln in lines[body_start:]:
            if ln in _DESCRIPTION_STOP_MARKERS or " · Ungefährer" in ln:
                break
            desc_lines.append(ln)
        description = "\n".join(desc_lines).strip() or None

    posted_at = location = None
    for pattern in _POSTED_PATTERNS:
        m = pattern.search(text)
        if m:
            posted_at = m.group("posted_at").strip() or None
            location = m.group("location").strip()
            break

    return {"condition": condition, "description": description, "posted_at": posted_at, "location": location}


# Some listings belong to a special Marketplace category - rentals being the
# main one seen so far - that changes both the page layout (no condition, no
# relative post date, a "for rent" label and a differently-labelled location
# box instead of the usual sentence) and the price's meaning ("CHF450" is
# per-something, not a one-off sale price). None of that is visible in the
# fields this scraper otherwise extracts, so it silently produced nulls
# instead of wrong data - still misleading (confirmed by testing: a real
# rental listing's condition/description/posted_at all read None even
# though the page clearly has a description, just under a different label).
#
# The category itself is language-independent: every listing's own page
# links back to its category via a plain URL slug (e.g.
# "/marketplace/109886099040554/propertyrentals/"), unlike normal for-sale
# listings which only link back to the bare, slug-less city anchor
# ("/marketplace/109886099040554/"). Reading that slug instead of any
# on-page text works regardless of the account's UI language.
_CATEGORY_HREF_RE = re.compile(r"^/marketplace/\d+/([a-z_]+)/?$")

# The period suffix Facebook shows after a rental price ("CHF450/month",
# "CHF450/Monat", "CHF450/mois", ...) is kept verbatim rather than
# translated to English - same "raw pass-through" treatment as `condition`
# and `description` elsewhere in this module.
_PRICE_PERIOD_RE = re.compile(r"(?:[0-9][0-9'.]*\s*[A-Za-z]{2,5}|[A-Za-z]{2,5}[0-9][0-9,]*)/(?P<period>\w+)")


def _extract_category(page: Page) -> str | None:
    """The Marketplace category slug this listing's own page links back to
    (e.g. "propertyrentals"), or None for a plain for-sale listing that only
    links back to the bare city anchor - see _CATEGORY_HREF_RE's comment."""
    try:
        hrefs = page.evaluate(
            """() => {
                const main = document.querySelector('div[role="main"]');
                if (!main) return [];
                return Array.from(main.querySelectorAll('a[href]')).map(a => a.getAttribute('href'));
            }"""
        )
    except Exception:
        return None
    for href in hrefs:
        m = _CATEGORY_HREF_RE.match(href or "")
        if m:
            return m.group(1)
    return None


def _extract_gallery_images(page: Page) -> list[str]:
    """Every full-size image in the listing's own photo gallery, in DOM
    order, stopping before Facebook's related-listings rail so those
    thumbnails don't leak in. The rail is always headed by the *last*
    <h2> on the page (confirmed by testing - "Heutige Auswahl" in German,
    "Today's picks" in English, in both cases the final h2), so this stops
    there structurally rather than matching either translation."""
    try:
        return page.evaluate(
            """() => {
                const main = document.querySelector('div[role="main"]');
                if (!main) return [];
                const h2s = main.querySelectorAll('h2');
                const stopNode = h2s.length ? h2s[h2s.length - 1] : null;
                const walker = document.createTreeWalker(main, NodeFilter.SHOW_ELEMENT);
                const urls = [];
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    if (stopNode && node === stopNode) break;
                    if (node.tagName === 'IMG' && node.src && node.src.includes('scontent')) {
                        urls.push(node.src);
                    }
                }
                return [...new Set(urls)];
            }"""
        )
    except Exception:
        return []


# --- Seller info (name, photo, join date, other listings) ------------------
#
# Structural, same approach and same confirmed layout as
# _DETAIL_STRUCTURE_JS above: the seller-info section always sits between
# the second-to-last and last <h2> on the page (see that function's big
# comment for why counting from the back is what handles rental listings'
# extra header). Within that range (confirmed by testing a real listing):
# the seller's name is the visible text of the one <a href="/marketplace/
# profile/<id>/..."> that has a non-empty aria-label (an identical link
# also wraps a generic, non-visible "Seller details" accessibility label -
# excluded here by requiring aria-label, since that's set to the seller's
# actual name, not a translated word); the profile photo is the sole
# <image xlink:href="..."> (an SVG-clipped avatar, not a plain <img>) in
# that range; and "Joined Facebook in <year>" (or whatever the account's
# language renders it as) is the one remaining plain text leaf, once the
# header's own label, the name link, and the "Message Seller" CTA (excluded
# via its role="button" ancestor, same helper idea as the description
# extraction) are excluded. Kept verbatim rather than parsed for a year,
# same "raw pass-through" treatment as `condition`/`description` elsewhere.
_SELLER_INFO_JS = """
() => {
    const main = document.querySelector('div[role="main"]');
    if (!main) return {matched: false, name: null, profileHref: null, photoUrl: null, joined: null};

    const h2s = Array.from(main.querySelectorAll('h2'));
    if (h2s.length < 2) return {matched: false, name: null, profileHref: null, photoUrl: null, joined: null};
    const sellerHeader = h2s[h2s.length - 2];
    const picksHeader = h2s[h2s.length - 1];

    const inRange = (el) => {
        if (sellerHeader.contains(el) || el === sellerHeader) return false;
        if (picksHeader.contains(el) || el === picksHeader) return false;
        const afterHeader = !!(sellerHeader.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
        const beforePicks = !!(picksHeader.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING);
        return afterHeader && beforePicks;
    };

    const profileLink = Array.from(main.querySelectorAll('a[href*="/marketplace/profile/"]'))
        .find((a) => inRange(a) && a.getAttribute('aria-label'));
    const name = profileLink ? profileLink.getAttribute('aria-label') : null;
    const profileHref = profileLink ? profileLink.getAttribute('href') : null;

    const photoImg = Array.from(main.querySelectorAll('image')).find((img) => inRange(img));
    const photoUrl = photoImg ? (photoImg.getAttribute('xlink:href') || photoImg.getAttribute('href')) : null;

    const isButtonAncestor = (el) => {
        let anc = el;
        for (let i = 0; i < 4 && anc; i++) {
            if (anc.getAttribute && anc.getAttribute('role') === 'button') return true;
            anc = anc.parentElement;
        }
        return false;
    };
    const isTextLeaf = (el) => {
        for (const child of el.children) {
            if (child.innerText && child.innerText.trim()) return false;
        }
        return true;
    };
    const joinedLeaf = Array.from(main.querySelectorAll('div, span')).find((el) => {
        if (!inRange(el) || !isTextLeaf(el)) return false;
        if (!el.innerText || !el.innerText.trim()) return false;
        if (el.parentElement && el.parentElement.tagName === 'A') return false;
        if (name && el.innerText.trim() === name) return false;
        return !isButtonAncestor(el);
    });
    const joined = joinedLeaf ? joinedLeaf.innerText.trim() : null;

    return {matched: true, name, profileHref, photoUrl, joined};
}
"""


def _extract_seller_info(page: Page) -> dict[str, Any]:
    try:
        result = page.evaluate(_SELLER_INFO_JS)
    except Exception:
        result = None
    return result or {"matched": False, "name": None, "profileHref": None, "photoUrl": None, "joined": None}


def _fetch_seller_listing_ids(page: Page, seller_name: str | None, max_scrolls: int = 6) -> list[str]:
    """Click the seller's name to open Marketplace's own "<name>'s listings"
    dialog (confirmed by testing - this is the only place Marketplace shows
    a seller's full item count/listing outside visiting their own listings
    one by one) and collect every listing id shown there, scrolling for more
    the same way scroll_to_load() does for the main search grid. Best-effort:
    returns [] (never raises) if there's no seller name to click, the dialog
    doesn't open, or the layout doesn't match - this is enrichment on top of
    the primary detail extraction, not something that should abort it."""
    if not seller_name:
        return []
    try:
        link = page.get_by_role("link", name=seller_name, exact=True).first
        if not link.is_visible(timeout=1000):
            return []
        link.click(timeout=3000)
        page.wait_for_selector('div[role="dialog"]', timeout=5000)
        try:
            # The dialog shell renders before its listings grid does
            # (confirmed by testing - a fixed short wait after the dialog
            # itself appears sometimes ran before any item was in the DOM
            # yet); wait for the first item link specifically instead of a
            # blind sleep. Not fatal if this never appears (e.g. a seller
            # who genuinely has none right now) - the scroll loop below
            # would just find nothing either way.
            page.wait_for_selector('div[role="dialog"] a[href*="/marketplace/item/"]', timeout=4000)
        except Exception:
            pass
    except Exception:
        return []

    ids: list[str] = []
    try:
        for _ in range(max_scrolls):
            hrefs = page.evaluate(
                """() => {
                    const dialog = document.querySelector('div[role="dialog"]');
                    if (!dialog) return [];
                    return Array.from(dialog.querySelectorAll('a[href*="/marketplace/item/"]'))
                        .map((a) => a.getAttribute('href'));
                }"""
            )
            before = len(ids)
            seen = set(ids)
            for href in hrefs:
                m = ITEM_RE.search(href or "")
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    ids.append(m.group(1))
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(600)
            if len(ids) == before:
                break
    except Exception:
        pass
    finally:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            pass
    return ids


def fetch_detail(
    page: Page, listing_id: str, verbose: bool = False, fetch_seller_listings: bool = True
) -> dict[str, Any]:
    """Visit one listing's own page and extract everything the search tile
    doesn't have: condition, full description, relative post date, the
    full-size image gallery, and the seller (name, profile photo, when they
    joined Facebook, and - if `fetch_seller_listings` - how many items
    they're currently selling and a link to every one of them). Returns a
    plain dict; any field Facebook didn't show for this listing is None (or
    [] for images/seller_listing_urls), never a KeyError.

    Extraction is structural (DOM shape, not translated words) via
    _extract_detail_structural() - see its docstring - which falls back to
    the legacy word-matching _parse_detail_text() only if the page doesn't
    have the expected layout at all. Seller info is always structural (see
    _SELLER_INFO_JS) since it was never covered by the legacy fallback.

    `fetch_seller_listings=False` skips clicking into the seller's "other
    listings" dialog (see _fetch_seller_listing_ids) - the name/photo/joined
    fields are already on the page and stay populated either way; this only
    controls the slower, extra-navigation part."""
    page.goto(listing_url(listing_id), wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    _raise_if_blocked(page, f"listing {listing_id}")
    dismiss_overlays(page)
    for label in ("Mehr ansehen", "See more", "Voir plus", "En voir plus"):
        try:
            more = page.get_by_text(label, exact=True).first
            if more.is_visible(timeout=1000):
                more.click(timeout=1000)
                page.wait_for_timeout(300)
                break
        except Exception:
            pass

    try:
        text = page.locator('div[role="main"]').first.inner_text(timeout=5000)
    except Exception:
        text = ""

    structural = _extract_detail_structural(page)
    got_something = any(structural.get(k) for k in ("condition", "description", "postedAt", "location"))
    if structural["matched"] and got_something:
        detail: dict[str, Any] = {
            "title": structural["title"],
            "condition": structural["condition"],
            "description": structural["description"],
            "posted_at": structural["postedAt"],
            "location": structural["location"],
        }
    else:
        # Either the page didn't have the expected h1/h2 layout at all, or
        # it did but nothing useful came out of it - confirmed by testing on
        # a rental listing whose location sits under its own extra h2
        # (rather than the description section, unlike a normal listing)
        # and whose description had auto-linked substrings (e.g. a mention)
        # splitting it across nodes with no single clean text leaf. Falling
        # back here recovers *something* via the older word-matching
        # approach rather than accepting an all-null result outright.
        detail = dict(_parse_detail_text(text))
        detail["title"] = structural["title"]
        if not detail["title"]:
            try:
                raw_title = page.title()
                if raw_title and raw_title != "Facebook":
                    cleaned = _TITLE_SUFFIX_RE.sub("", raw_title)
                    cleaned = _TITLE_PREFIX_RE.sub("", cleaned)
                    cleaned = _TITLE_TRAILING_FACEBOOK_RE.sub("", cleaned)
                    detail["title"] = cleaned.strip() or None
            except Exception:
                pass

    detail["images"] = _extract_gallery_images(page)
    detail["category"] = _extract_category(page)
    detail["is_rental"] = bool(detail["category"]) and "rental" in detail["category"]
    period_match = _PRICE_PERIOD_RE.search(text)
    detail["price_period"] = period_match.group("period") if period_match else None

    seller = _extract_seller_info(page)
    detail["seller_name"] = seller["name"]
    profile_match = PROFILE_RE.search(seller["profileHref"] or "")
    detail["seller_profile_url"] = seller_profile_url(profile_match.group(1)) if profile_match else None
    detail["seller_photo_url"] = seller["photoUrl"]
    detail["seller_joined"] = seller["joined"]
    seller_listing_ids = _fetch_seller_listing_ids(page, seller["name"]) if fetch_seller_listings else []
    detail["seller_listing_count"] = len(seller_listing_ids) if seller_listing_ids else None
    detail["seller_listing_urls"] = [listing_url(sid) for sid in seller_listing_ids]

    return detail


def visit_all_listings(
    page: Page,
    listings: list[Listing],
    delay: float = 0.4,
    verbose: bool = True,
    fetch_seller_listings: bool = True,
) -> list[Listing]:
    """Visit each listing's own page one by one and merge in fetch_detail()'s
    fields. Search-result title/price/location win over detail-page values
    (they're already reliable - see search_listings()); a listing with no title
    (common - many listings just show a price, no headline) is backfilled
    from the detail page's <title>, same spirit as AutoScout24Scraper's
    seller-object backfill in its own visit_all_listings()."""
    visited: list[Listing] = []
    total = len(listings)
    for i, item in enumerate(listings, 1):
        detail = fetch_detail(page, item["listing_id"], fetch_seller_listings=fetch_seller_listings)
        merged = dict(item)
        if not merged.get("title") and detail.get("title"):
            merged["title"] = detail["title"]
        merged["condition"] = detail.get("condition")
        merged["description"] = detail.get("description")
        merged["posted_at"] = detail.get("posted_at")
        merged["images"] = detail.get("images") or []
        merged["category"] = detail.get("category")
        merged["is_rental"] = detail.get("is_rental", False)
        merged["price_period"] = detail.get("price_period")
        merged["seller_name"] = detail.get("seller_name")
        merged["seller_profile_url"] = detail.get("seller_profile_url")
        merged["seller_photo_url"] = detail.get("seller_photo_url")
        merged["seller_joined"] = detail.get("seller_joined")
        merged["seller_listing_count"] = detail.get("seller_listing_count")
        merged["seller_listing_urls"] = detail.get("seller_listing_urls") or []
        visited.append(merged)
        if verbose and (i % 5 == 0 or i == total):
            logger.info("  visited %d/%d listings (id=%s)", i, total, item["listing_id"])
        if i < total:
            time.sleep(delay)
    return visited


PRIORITY_FIELDS = [
    "listing_id",
    "title",
    "price",
    "original_price",
    "price_period",
    "is_rental",
    "condition",
    "location",
    "is_local",
    "posted_at",
    "url",
    "image_url",
    "images",
    "description",
    "seller_name",
    "seller_profile_url",
    "seller_photo_url",
    "seller_joined",
    "seller_listing_count",
    "seller_listing_urls",
    "category",
    "country",
]


def flatten_listing(item: Listing) -> dict[str, Any]:
    """Flatten one listing dict into something that fits a CSV row - list
    values (`images`, `seller_listing_urls`) are joined into one
    semicolon-separated cell, same convention as AutoScout24Scraper's list
    fields (`features`, `images`)."""
    flat = dict(item)
    for key, value in flat.items():
        if isinstance(value, list):
            flat[key] = "; ".join(value)
    return flat


def order_fieldnames(all_keys: set[str]) -> list[str]:
    ordered = [f for f in PRIORITY_FIELDS if f in all_keys]
    remaining = sorted(k for k in all_keys if k not in ordered)
    return ordered + remaining


def save_csv(rows: list[dict[str, Any]], path: str) -> None:
    if not rows:
        logger.warning("no rows to write")
        return
    all_keys: set[str] = set()
    for row in rows:
        all_keys.update(row.keys())
    fieldnames = order_fieldnames(all_keys)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)


def save_json(rows: list[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def _price_number(price: str | None) -> int | None:
    """'16.900\\xa0CHF' -> 16900, for sorting; None if unparseable."""
    if not price:
        return None
    digits = "".join(PRICE_DIGITS_RE.findall(price))
    return int(digits) if digits else None


@dataclass
class ScrapeResult:
    """Everything a scrape() call produced, ready to use in-memory or save
    to disk. Mirrors AutoScout24Scraper's ScrapeResult shape/method names on
    purpose so code can switch between the two scrapers with minimal changes."""

    query: str
    country: str
    total_elements: int
    listings: list[Listing] = field(default_factory=list)  # one dict per listing (see README -> Data structure)
    rows: list[dict[str, Any]] = field(default_factory=list)  # flattened dicts, one per listing, CSV-ready

    def to_csv(self, path: str) -> None:
        save_csv(self.rows, path)

    def to_json(self, path: str) -> None:
        save_json(self.listings, path)


def scrape(
    query: str,
    *,
    country: str = config.DEFAULT_COUNTRY,
    detail: bool = True,
    min_price: int | None = None,
    max_price: int | None = None,
    min_mileage: int | None = None,
    max_mileage: int | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    condition: str | list[str] | None = None,
    city: str | None = None,
    radius_km: int | None = None,
    keep_account_radius: bool = False,
    local_only: bool = True,
    delay: float = 0.4,
    max_scrolls: int = DEFAULT_MAX_SCROLLS,
    split_threshold: int | None = SPLIT_THRESHOLD,
    fetch_seller_listings: bool = True,
    verbose: bool = True,
    headless: bool = True,
    session: BrowserContext | None = None,
    email: str | None = None,
    password: str | None = None,
) -> ScrapeResult:
    """Search Facebook Marketplace and return the results in memory.

    This is the library entry point: it does the same work as the CLI but
    returns a ScrapeResult instead of writing files. The CLI (main.py) is a
    thin wrapper around this function - same relationship as
    AutoScout24Scraper's main()/scrape().

    Args:
        query: Free text search, e.g. "Tesla Model S" or "iPhone 15" -
            exactly what you'd type into the Marketplace search box.
        country: Which COUNTRY_ANCHORS entry to search from (default "ch").
            Only "ch" is implemented today - see config.py / README.
        detail: If True (default), visit every listing's own page for
            condition/description/post date/full image gallery. If False,
            keep only the summary fields from the search tiles (faster).
        min_price/max_price: Optional price range, inclusive.
        min_year/max_year/min_mileage/max_mileage: Ignored - accepted only
            so existing callers don't break; a warning is logged if any is
            given, and listings of every year and mileage are returned.
            Facebook only applies these filters to listings posted with
            structured vehicle data and silently drops every other listing
            (confirmed by testing: a year filter kept 38 of 358 "Tesla Model
            X" listings, dropping e.g. a 2017 Model X whose year was only in
            its title), and the listing data has no year or mileage field
            to filter on locally either.
        condition: Optional item condition filter - one of "new",
            "used_like_new", "used_good", "used_fair", or a list of them.
        city: City to search around, e.g. "Bern" or "Genève" - looked up
            with lookup_city(), which takes the first place Facebook's own
            location search suggests inside `country` (possibly a nearby
            town or neighbourhood). A numeric Facebook location id is used
            as-is. Defaults to the country's anchor city (Zürich for "ch").
            Doesn't change the account's own location.
        radius_km: Search radius in km, any positive number. Rounded up to
            the closest radius Facebook offers (ALLOWED_RADII_KM: 30 -> 40,
            101 -> 250), or its maximum, 500, for anything bigger. Defaults
            to the country's radius in config.COUNTRY_ANCHORS (500 for
            "ch"). Facebook only honours the radius saved on the account,
            so this *changes that account setting* if it differs (the same
            one you see in your own browser) - see
            set_account_search_radius(). If that fails, a warning is logged
            and the search runs with whatever radius the account has.
        keep_account_radius: If True, never change the account's radius -
            search with whatever it's set to. `radius_km` is then ignored.
        local_only: If True (default), drop listings whose location doesn't
            look like it's actually inside `country` (Facebook's radius
            search can spill just over a border).
        delay: Seconds to wait between detail-page visits.
        max_scrolls: Safety cap on how many times to scroll one search
            page for more listings. Scrolling normally stops sooner, when
            Facebook says there are no more results.
        split_threshold: If a search returns at least this many listings
            (default SPLIT_THRESHOLD), search again in smaller price ranges
            and merge the results - Facebook ends big searches early. None
            disables this. See search_all_listings().
        fetch_seller_listings: If True (default) and `detail` is also True,
            click into each seller's own "<name>'s listings" dialog to
            collect how many items they're currently selling and a link to
            every one of them (`seller_listing_count`/`seller_listing_urls`).
            The seller's name/photo/join date are extracted either way -
            this only gates the slower part. Set False to skip it and speed
            up detail visits.
        verbose: If True, print progress to stdout.
        headless: Whether to run the browser headless. Ignored if `session`
            is given.
        session: An existing Playwright BrowserContext to reuse (e.g. across
            repeated calls), same idea as AutoScout24Scraper's
            `session: requests.Session | None`. A new one is opened (and
            closed afterwards) if not given. Ignored if `session` is given
            (login is then whatever that context already has).
        email/password: Facebook credentials to log in with if not already
            logged in - fills and submits Facebook's own login form. Only
            works if Facebook doesn't challenge the login with a
            2FA/checkpoint step; raises `fb_scraper.browser.LoginFailedError`
            if it does (run with `headless=False` and log in by hand
            instead in that case). Ignored if `session` is given, or if
            already logged in. Prerequisite: this account must have already
            confirmed Marketplace's one-time consent dialog (personalised/
            personalized profile option) via a `headless=False` run at least
            once before - that dialog can't be automated, so a brand new
            account will raise `MarketplaceConsentRequiredError` on the
            first search/detail call even with fully correct credentials.

    Returns:
        A ScrapeResult with `.listings` (one dict per listing) and `.rows`
        (flattened, CSV-ready, sorted by price ascending).
    """
    for lo_name, hi_name, lo, hi in (("min_price", "max_price", min_price, max_price),):
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"{lo_name} ({lo}) cannot be greater than {hi_name} ({hi})")
    ignored = {
        name: value
        for name, value in (
            ("min_year", min_year),
            ("max_year", max_year),
            ("min_mileage", min_mileage),
            ("max_mileage", max_mileage),
        )
        if value is not None
    }
    if ignored:
        logger.warning(
            "Ignoring %s: year and mileage filters aren't supported (Facebook drops every listing without "
            "structured vehicle data) - returning listings of every year and mileage.",
            ", ".join(f"{name}={value}" for name, value in ignored.items()),
        )

    anchor = config.anchor_for(country)  # raises ValueError immediately if unknown
    target_radius = None
    if not keep_account_radius:
        requested_radius = anchor["radius_km"] if radius_km is None else radius_km
        target_radius = supported_radius_km(requested_radius)  # ValueError for <= 0, before opening a browser
        if verbose and target_radius != requested_radius:
            logger.info(
                "Search radius %g km -> %d km (%s)",
                requested_radius,
                target_radius,
                "Facebook's maximum" if requested_radius > ALLOWED_RADII_KM[-1] else "the closest Facebook offers",
            )

    def _run(context: BrowserContext) -> tuple[list[Listing], int]:
        page = context.new_page()
        try:
            if verbose:
                logger.info("Searching Marketplace for %r (country=%r) ...", query, country)
            location = None
            if city and city.strip().isdigit():
                location = city.strip()
            elif city:
                location, place = lookup_city(page, city.strip(), country)
                if verbose:
                    logger.info("  city %r -> %s (Facebook location %s)", city, place, location)
            if target_radius is not None:
                try:
                    set_account_search_radius(page, target_radius, query, country, verbose=verbose, location=location)
                except (SearchRadiusError, PlaywrightError) as e:
                    logger.warning(
                        "  couldn't set the search radius to %d km (%s) - searching with the account's current "
                        "radius instead. Set it by hand under Marketplace -> Location, or pass "
                        "--keep-account-radius / keep_account_radius=True to skip this step.",
                        target_radius,
                        str(e).splitlines()[0],
                    )
            found = search_all_listings(
                page,
                query,
                country=country,
                min_price=min_price,
                max_price=max_price,
                split_threshold=split_threshold,
                condition=condition,
                location=location,
                max_scrolls=max_scrolls,
                verbose=verbose,
            )
            if local_only:
                before = len(found)
                found = [x for x in found if x.get("is_local")]
                if verbose and len(found) != before:
                    logger.info("  kept %d/%d listings that look like they're in %r", len(found), before, country)
            n = len(found)
            if detail:
                if verbose:
                    logger.info("Visiting each of %d listings individually for full details ...", n)
                found = visit_all_listings(
                    page, found, delay=delay, verbose=verbose, fetch_seller_listings=fetch_seller_listings
                )
            return found, n
        finally:
            page.close()

    if session is not None:
        listings, total_elements = _run(session)
    else:
        from .browser import FacebookSession

        with FacebookSession(headless=headless, email=email, password=password) as context:
            listings, total_elements = _run(context)

    rows = [flatten_listing(item) for item in listings]
    rows.sort(key=lambda r: (_price_number(r.get("price")) is None, _price_number(r.get("price")) or 0))

    return ScrapeResult(query=query, country=country, total_elements=total_elements, listings=listings, rows=rows)
