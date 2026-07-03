"""
Facebook Marketplace scraper core.

Unlike AutoScout24 (a separate, unauthenticated JSON API subdomain,
api.autoscout24.ch), Facebook has no such API: plain HTTP requests are
blocked before any application logic runs (HTTP 400, no cookies set - this
looks like TLS/browser-fingerprint level bot detection, confirmed by
testing), and network sniffing while browsing Marketplace shows no separate
XHR/GraphQL endpoint either - the listing grid is embedded directly in the
server-rendered HTML of the very first request. So this scraper drives a
real Playwright/Chromium browser (see browser.py) instead of calling an API
with `requests`. That is the one deliberate, tested architectural
difference from AutoScout24Scraper; everything else mirrors it on purpose:
generic field extraction, a two-phase search-then-detail pipeline, a
ScrapeResult dataclass, and a scrape() library entry point with the same
shape (query in, ScrapeResult out).

Discovered request shape (found by trying filters in Marketplace's own UI
and reading the resulting URL, the same "watch what the real frontend does"
technique that found AutoScout24's API):

  GET https://www.facebook.com/marketplace/{anchor}/search
      ?query=...              free text, required
      &radius=...             km from the anchor city
      &minPrice=/&maxPrice=   price range (any currency shown on the site)
      &minMileage=/&maxMileage=   km, vehicles
      &minYear=/&maxYear=     first-registration year, vehicles
      &itemCondition=a,b      comma-separated: new, used_like_new,
                               used_good, used_fair
      &sortBy=price_ascend    see below

Facebook Marketplace has no "whole country" search - every search needs a
city to anchor on, with a radius. This scraper always sorts by
`price_ascend`: without an explicit sort, Marketplace's default ranking
reshuffles which listings appear first between requests/scrolls (the same
"rotating boosted listing" problem AutoScout24's API has), which would make
scrolling for more results skip or duplicate listings. A stable sort makes
that deterministic; listings are also de-duplicated by id as a safety net,
exactly like AutoScout24Scraper's search_listings().

Logged-out browsing (no cookies, nothing to configure) already returns real
results, capped at ~24 per search with no further pagination on scroll.
Logging in once (`--headed`, see browser.py) removes that cap.

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

1. As a CLI (see main.py):
    python3 main.py --query "Tesla Model S"
    python3 main.py --query "iPhone 15" --no-detail
    python3 main.py --query "Tesla Model S" --price-to 30000 --year-from 2018

2. As a library:
    from fb_scraper.scraper import scrape
    result = scrape("Tesla Model S", price_to=30000)
    for row in result.rows:
        print(row["price"], row["url"])
"""
from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from . import config

ITEM_RE = re.compile(r"/marketplace/item/(\d+)")

# Facebook renders each search-result tile's full aria-label as one
# structured string: "<title (may be empty)>, <price>, <city>,
# <canton/region>, Inserat <id>" ("Inserat" = German "listing"; anchored on
# the trailing id rather than that word so a locale change doesn't break it).
ARIA_RE = re.compile(
    r"^(?P<title>.*),\s*"
    r"(?P<price>[0-9][0-9'.,]*\s*\S+),\s*"
    r"(?P<city>[^,]+),\s*"
    r"(?P<region>[^,]+),\s*"
    r"\D*(?P<id>\d+)$"
)

PRICE_DIGITS_RE = re.compile(r"\d+")


def listing_url(listing_id):
    return f"https://www.facebook.com/marketplace/item/{listing_id}/"


def build_search_url(query, country=config.DEFAULT_COUNTRY, *, min_price=None, max_price=None,
                      min_mileage=None, max_mileage=None, min_year=None, max_year=None,
                      condition=None):
    anchor = config.anchor_for(country)
    params = {
        "query": query,
        "exact": "false",
        "radius": anchor["radius_km"],
        "sortBy": "price_ascend",
    }
    if min_price is not None:
        params["minPrice"] = min_price
    if max_price is not None:
        params["maxPrice"] = max_price
    if min_mileage is not None:
        params["minMileage"] = min_mileage
    if max_mileage is not None:
        params["maxMileage"] = max_mileage
    if min_year is not None:
        params["minYear"] = min_year
    if max_year is not None:
        params["maxYear"] = max_year
    if condition:
        params["itemCondition"] = ",".join(condition) if isinstance(condition, (list, tuple)) else condition
    return f"https://www.facebook.com/marketplace/{anchor['slug']}/search?{urlencode(params)}"


def _dismiss_overlays(page):
    """Best-effort dismissal of the cookie banner and login nag that
    otherwise sit on top of the page and intercept clicks/scrolls. Both are
    optional - logged-out browsing works without touching either."""
    for label in ["Optionale Cookies ablehnen", "Decline optional cookies"]:
        try:
            btn = page.get_by_text(label, exact=False).first
            if btn.is_visible(timeout=800):
                btn.click(timeout=800)
                page.wait_for_timeout(300)
        except Exception:
            pass
    for label in ["Schließen", "Close"]:
        try:
            btn = page.locator(f'div[aria-label="{label}"]').first
            if btn.is_visible(timeout=800):
                btn.click(timeout=800)
                page.wait_for_timeout(300)
        except Exception:
            pass


def scroll_to_load(page, max_scrolls=8, pause_ms=1500):
    """Scroll to trigger Marketplace's lazy-loaded results (logged-in only -
    logged-out search is a fixed ~24-result page and scrolling is a no-op,
    but harmless)."""
    last_height = 0
    for _ in range(max_scrolls):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(pause_ms)
        height = page.evaluate("document.body.scrollHeight")
        if height == last_height:
            break
        last_height = height


def parse_tile(anchor):
    """Parse one search-result <a href="/marketplace/item/..."> tag (a
    bs4 Tag) into a plain dict, or None if it's not actually a listing link."""
    href = anchor.get("href", "")
    href_match = ITEM_RE.search(href)
    if not href_match:
        return None
    listing_id = href_match.group(1)

    aria_label = anchor.get("aria-label", "") or ""
    title = price = city = region = None
    m = ARIA_RE.match(aria_label)
    if m:
        title = m.group("title").strip() or None
        price = m.group("price").strip()
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
        "location": location,
        "url": listing_url(listing_id),
        "image_url": image_url,
    }


def search_listings(page, query, country=config.DEFAULT_COUNTRY, *, min_price=None, max_price=None,
                     min_mileage=None, max_mileage=None, min_year=None, max_year=None,
                     condition=None, max_scrolls=8, verbose=True):
    """Fetch every listing matching `query`, de-duplicated by id. See the
    module docstring for why sortBy=price_ascend is always used."""
    url = build_search_url(
        query, country=country, min_price=min_price, max_price=max_price,
        min_mileage=min_mileage, max_mileage=max_mileage,
        min_year=min_year, max_year=max_year, condition=condition,
    )
    if verbose:
        print(f"  {url}")
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    _dismiss_overlays(page)
    scroll_to_load(page, max_scrolls=max_scrolls)

    soup = BeautifulSoup(page.content(), "lxml")
    anchors = soup.find_all("a", href=ITEM_RE)
    listings = []
    seen = set()
    for a in anchors:
        item = parse_tile(a)
        if not item or item["listing_id"] in seen:
            continue
        seen.add(item["listing_id"])
        item["country"] = country
        item["is_local"] = config.is_local(item.get("location"), country)
        listings.append(item)

    if verbose:
        print(f"  found {len(listings)} unique listings")
    return listings


_POSTED_RE = re.compile(r"Gepostet\s*(?P<posted_at>[^–\n-]*?)\s*[–-]\s*hier:\s*(?P<location>[^\n]+)")
_TITLE_SUFFIX_RE = re.compile(r"\s*[–-]\s*.*Facebook Marketplace.*$")

# Section headers Facebook uses above the condition/description block - seen
# both "Beschreibung durch den Verkäufer" (private sellers) and "Details"
# (business/rental listings). Description parsing anchors on the "Zustand"
# (condition) line instead, since that's present either way.
_DESCRIPTION_STOP_MARKERS = ("Mehr ansehen", "Nachricht senden", "Heutige Auswahl")


def _parse_detail_text(text):
    lines = [ln.strip() for ln in text.split("\n")]

    condition = None
    zustand_index = None
    for i, ln in enumerate(lines):
        if ln == "Zustand" and i + 1 < len(lines):
            zustand_index = i
            condition = lines[i + 1].strip() or None
            break

    description = None
    if zustand_index is not None:
        desc_lines = []
        for ln in lines[zustand_index + 2:]:
            if ln in _DESCRIPTION_STOP_MARKERS or " · Ungefährer" in ln:
                break
            desc_lines.append(ln)
        description = "\n".join(desc_lines).strip() or None

    posted_at = location = None
    m = _POSTED_RE.search(text)
    if m:
        posted_at = m.group("posted_at").strip() or None
        location = m.group("location").strip()

    return {"condition": condition, "description": description, "posted_at": posted_at, "location": location}


def _extract_gallery_images(page):
    """Every full-size image in the listing's own photo gallery, in DOM
    order, stopping before Facebook's "Heutige Auswahl" (today's picks)
    related-listings rail so those thumbnails don't leak in."""
    try:
        return page.evaluate(
            """() => {
                const main = document.querySelector('div[role="main"]');
                if (!main) return [];
                const walker = document.createTreeWalker(main, NodeFilter.SHOW_ELEMENT);
                const urls = [];
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    if (node.innerText && node.innerText.trim() === 'Heutige Auswahl') break;
                    if (node.tagName === 'IMG' && node.src && node.src.includes('scontent')) {
                        urls.push(node.src);
                    }
                }
                return [...new Set(urls)];
            }"""
        )
    except Exception:
        return []


def fetch_detail(page, listing_id, verbose=False):
    """Visit one listing's own page and extract everything the search tile
    doesn't have: condition, full description, relative post date, and the
    full-size image gallery. Returns a plain dict; any field Facebook didn't
    show for this listing is None (or [] for images), never a KeyError."""
    page.goto(listing_url(listing_id), wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    _dismiss_overlays(page)
    try:
        more = page.get_by_text("Mehr ansehen", exact=True).first
        if more.is_visible(timeout=1000):
            more.click(timeout=1000)
            page.wait_for_timeout(300)
    except Exception:
        pass

    title = None
    try:
        raw_title = page.title()
        if raw_title and raw_title != "Facebook":
            title = _TITLE_SUFFIX_RE.sub("", raw_title).strip() or None
    except Exception:
        pass

    try:
        text = page.locator('div[role="main"]').first.inner_text(timeout=5000)
    except Exception:
        text = ""

    detail = _parse_detail_text(text)
    detail["title"] = title
    detail["images"] = _extract_gallery_images(page)
    return detail


def visit_all_listings(page, listings, delay=0.4, verbose=True):
    """Visit each listing's own page one by one and merge in fetch_detail()'s
    fields. Tile-provided title/price/location win over detail-page values
    (they're already reliable - see parse_tile()); a tile with no title
    (common - many listings just show a price, no headline) is backfilled
    from the detail page's <title>, same spirit as AutoScout24Scraper's
    seller-object backfill in its own visit_all_listings()."""
    visited = []
    total = len(listings)
    for i, item in enumerate(listings, 1):
        detail = fetch_detail(page, item["listing_id"])
        merged = dict(item)
        if not merged.get("title") and detail.get("title"):
            merged["title"] = detail["title"]
        merged["condition"] = detail.get("condition")
        merged["description"] = detail.get("description")
        merged["posted_at"] = detail.get("posted_at")
        merged["images"] = detail.get("images") or []
        visited.append(merged)
        if verbose and (i % 5 == 0 or i == total):
            print(f"  visited {i}/{total} listings (id={item['listing_id']})")
        if i < total:
            time.sleep(delay)
    return visited


PRIORITY_FIELDS = [
    "listing_id", "title", "price", "condition", "location", "is_local",
    "posted_at", "url", "image_url", "images", "description", "country",
]


def flatten_listing(item):
    """Flatten one listing dict into something that fits a CSV row - the
    only nested value is `images` (a list), joined into one
    semicolon-separated cell, same convention as AutoScout24Scraper's list
    fields (`features`, `images`)."""
    flat = dict(item)
    images = flat.get("images")
    if isinstance(images, list):
        flat["images"] = "; ".join(images)
    return flat


def order_fieldnames(all_keys):
    ordered = [f for f in PRIORITY_FIELDS if f in all_keys]
    remaining = sorted(k for k in all_keys if k not in ordered)
    return ordered + remaining


def save_csv(rows, path):
    if not rows:
        print("  [warn] no rows to write")
        return
    all_keys = set()
    for row in rows:
        all_keys.update(row.keys())
    fieldnames = order_fieldnames(all_keys)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)


def save_json(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def _price_number(price):
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
    listings: list = field(default_factory=list)  # one dict per listing (see README -> Data structure)
    rows: list = field(default_factory=list)       # flattened dicts, one per listing, CSV-ready

    def to_csv(self, path):
        save_csv(self.rows, path)

    def to_json(self, path):
        save_json(self.listings, path)


def scrape(query, *, country=config.DEFAULT_COUNTRY, detail=True,
           min_price=None, max_price=None,
           min_mileage=None, max_mileage=None,
           min_year=None, max_year=None,
           condition=None, local_only=True,
           delay=0.4, max_scrolls=8, verbose=True,
           headless=True, session=None):
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
        min_mileage/max_mileage: Optional mileage range in km, inclusive -
            only meaningful for vehicle listings; harmless no-op filter for
            other item types (Facebook just won't have anything with a
            mileage attribute to match).
        min_year/max_year: Optional first-registration year range,
            inclusive - vehicles only, same caveat as mileage.
        condition: Optional item condition filter - one of "new",
            "used_like_new", "used_good", "used_fair", or a list of them.
        local_only: If True (default), drop listings whose location doesn't
            look like it's actually inside `country` (Facebook's radius
            search can spill just over a border).
        delay: Seconds to wait between detail-page visits.
        max_scrolls: How many times to scroll the search results looking
            for more listings (only matters when logged in - see browser.py).
        verbose: If True, print progress to stdout.
        headless: Whether to run the browser headless. Ignored if `session`
            is given.
        session: An existing Playwright BrowserContext to reuse (e.g. across
            repeated calls), same idea as AutoScout24Scraper's
            `session: requests.Session | None`. A new one is opened (and
            closed afterwards) if not given.

    Returns:
        A ScrapeResult with `.listings` (one dict per listing) and `.rows`
        (flattened, CSV-ready, sorted by price ascending).
    """
    for lo_name, hi_name, lo, hi in (
        ("min_price", "max_price", min_price, max_price),
        ("min_mileage", "max_mileage", min_mileage, max_mileage),
        ("min_year", "max_year", min_year, max_year),
    ):
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"{lo_name} ({lo}) cannot be greater than {hi_name} ({hi})")

    config.anchor_for(country)  # raises ValueError immediately if unknown

    def _run(context):
        page = context.new_page()
        try:
            if verbose:
                print(f"Searching Marketplace for {query!r} (country={country!r}) ...")
            found = search_listings(
                page, query, country=country,
                min_price=min_price, max_price=max_price,
                min_mileage=min_mileage, max_mileage=max_mileage,
                min_year=min_year, max_year=max_year,
                condition=condition, max_scrolls=max_scrolls, verbose=verbose,
            )
            if local_only:
                before = len(found)
                found = [x for x in found if x.get("is_local")]
                if verbose and len(found) != before:
                    print(f"  kept {len(found)}/{before} listings that look like they're in {country!r}")
            n = len(found)
            if detail:
                if verbose:
                    print(f"Visiting each of {n} listings individually for full details ...")
                found = visit_all_listings(page, found, delay=delay, verbose=verbose)
            return found, n
        finally:
            page.close()

    if session is not None:
        listings, total_elements = _run(session)
    else:
        from .browser import FacebookSession
        with FacebookSession(headless=headless) as context:
            listings, total_elements = _run(context)

    rows = [flatten_listing(item) for item in listings]
    rows.sort(key=lambda r: (_price_number(r.get("price")) is None, _price_number(r.get("price")) or 0))

    return ScrapeResult(query=query, country=country, total_elements=total_elements,
                         listings=listings, rows=rows)
