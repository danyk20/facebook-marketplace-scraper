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
    facebook-marketplace-scraper --query "Tesla Model S" --price-to 30000 --year-from 2018

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
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.sync_api import BrowserContext, Page

from . import config
from .browser import dismiss_overlays

logger = logging.getLogger(__name__)

Listing = dict[str, Any]

ITEM_RE = re.compile(r"/marketplace/item/(\d+)")

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
ARIA_RE = re.compile(
    r"^(?P<title>.*),\s*"
    r"(?P<price>[0-9][0-9'.]*\s*[A-Za-z]{2,5}|[A-Za-z]{2,5}[0-9][0-9,]*),\s*"
    r"(?P<city>[^,]+),\s*"
    r"(?P<region>[^,]+),\s*"
    r"\D*(?P<id>\d+)$"
)

PRICE_DIGITS_RE = re.compile(r"\d+")


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


def listing_url(listing_id: str | int) -> str:
    return f"https://www.facebook.com/marketplace/item/{listing_id}/"


def build_search_url(
    query: str,
    country: str = config.DEFAULT_COUNTRY,
    *,
    min_price: int | None = None,
    max_price: int | None = None,
    min_mileage: int | None = None,
    max_mileage: int | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    condition: str | list[str] | None = None,
) -> str:
    anchor = config.anchor_for(country)
    params: dict[str, Any] = {
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


def scroll_to_load(page: Page, max_scrolls: int = 8, pause_ms: int = 1500) -> None:
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


def parse_tile(anchor: Tag) -> Listing | None:
    """Parse one search-result <a href="/marketplace/item/..."> tag (a
    bs4 Tag) into a plain dict, or None if it's not actually a listing link."""
    href = str(anchor.get("href", ""))
    href_match = ITEM_RE.search(href)
    if not href_match:
        return None
    listing_id = href_match.group(1)

    aria_label = str(anchor.get("aria-label") or "")
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


def search_listings(
    page: Page,
    query: str,
    country: str = config.DEFAULT_COUNTRY,
    *,
    min_price: int | None = None,
    max_price: int | None = None,
    min_mileage: int | None = None,
    max_mileage: int | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    condition: str | list[str] | None = None,
    max_scrolls: int = 8,
    verbose: bool = True,
) -> list[Listing]:
    """Fetch every listing matching `query`, de-duplicated by id. See the
    module docstring for why sortBy=price_ascend is always used."""
    url = build_search_url(
        query,
        country=country,
        min_price=min_price,
        max_price=max_price,
        min_mileage=min_mileage,
        max_mileage=max_mileage,
        min_year=min_year,
        max_year=max_year,
        condition=condition,
    )
    if verbose:
        logger.info("  %s", url)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    _raise_if_blocked(page, "the search results")
    dismiss_overlays(page)
    scroll_to_load(page, max_scrolls=max_scrolls)

    soup = BeautifulSoup(page.content(), "lxml")
    anchors = soup.find_all("a", href=ITEM_RE)
    listings: list[Listing] = []
    seen: set[str] = set()
    for a in anchors:
        item = parse_tile(a)
        if not item or item["listing_id"] in seen:
            continue
        seen.add(item["listing_id"])
        item["country"] = country
        item["is_local"] = config.is_local(item.get("location"), country)
        listings.append(item)

    if verbose:
        logger.info("  found %d unique listings", len(listings))
    return listings


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


def fetch_detail(page: Page, listing_id: str, verbose: bool = False) -> dict[str, Any]:
    """Visit one listing's own page and extract everything the search tile
    doesn't have: condition, full description, relative post date, and the
    full-size image gallery. Returns a plain dict; any field Facebook didn't
    show for this listing is None (or [] for images), never a KeyError.

    Extraction is structural (DOM shape, not translated words) via
    _extract_detail_structural() - see its docstring - which falls back to
    the legacy word-matching _parse_detail_text() only if the page doesn't
    have the expected layout at all."""
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
    return detail


def visit_all_listings(page: Page, listings: list[Listing], delay: float = 0.4, verbose: bool = True) -> list[Listing]:
    """Visit each listing's own page one by one and merge in fetch_detail()'s
    fields. Tile-provided title/price/location win over detail-page values
    (they're already reliable - see parse_tile()); a tile with no title
    (common - many listings just show a price, no headline) is backfilled
    from the detail page's <title>, same spirit as AutoScout24Scraper's
    seller-object backfill in its own visit_all_listings()."""
    visited: list[Listing] = []
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
        merged["category"] = detail.get("category")
        merged["is_rental"] = detail.get("is_rental", False)
        merged["price_period"] = detail.get("price_period")
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
    "category",
    "country",
]


def flatten_listing(item: Listing) -> dict[str, Any]:
    """Flatten one listing dict into something that fits a CSV row - the
    only nested value is `images` (a list), joined into one
    semicolon-separated cell, same convention as AutoScout24Scraper's list
    fields (`features`, `images`)."""
    flat = dict(item)
    images = flat.get("images")
    if isinstance(images, list):
        flat["images"] = "; ".join(images)
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
    local_only: bool = True,
    delay: float = 0.4,
    max_scrolls: int = 8,
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
    for lo_name, hi_name, lo, hi in (
        ("min_price", "max_price", min_price, max_price),
        ("min_mileage", "max_mileage", min_mileage, max_mileage),
        ("min_year", "max_year", min_year, max_year),
    ):
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"{lo_name} ({lo}) cannot be greater than {hi_name} ({hi})")

    config.anchor_for(country)  # raises ValueError immediately if unknown

    def _run(context: BrowserContext) -> tuple[list[Listing], int]:
        page = context.new_page()
        try:
            if verbose:
                logger.info("Searching Marketplace for %r (country=%r) ...", query, country)
            found = search_listings(
                page,
                query,
                country=country,
                min_price=min_price,
                max_price=max_price,
                min_mileage=min_mileage,
                max_mileage=max_mileage,
                min_year=min_year,
                max_year=max_year,
                condition=condition,
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
                found = visit_all_listings(page, found, delay=delay, verbose=verbose)
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
