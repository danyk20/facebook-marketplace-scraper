"""
Shared fixtures for the unit test suite.

Unlike AutoScout24Scraper (pure `requests`, mocked with the `responses`
library), this scraper drives a real Playwright browser - there's no HTTP
layer to intercept with `responses`. The equivalent here is
BrowserContext.route(): a real (headless) Chromium still runs, but every
request is intercepted and answered with canned HTML instead of touching
facebook.com, so these tests need no network access and never depend on
the live site's markup. That's the whole unit vs. e2e split (see
test_e2e.py): unit tests hit this fixture's fake pages, e2e tests hit the
real site.
"""

import json
import re
from urllib.parse import parse_qs

import pytest
from playwright.sync_api import sync_playwright

from fb_scraper import scraper

ITEM_ID_RE = re.compile(r"/marketplace/item/(\d+)")

DEFAULT_SEARCH_HTML = """
<html><body>
<a href="/marketplace/item/111/?ref=x" aria-label="Cool Item, 1.000 CHF, Zürich, ZH, Inserat 111">
  <img src="https://scontent.example.net/thumb111.jpg">
</a>
<a href="/marketplace/item/222/?ref=x" aria-label=", 2.000 CHF, Bern, BE, Inserat 222">
  <img src="https://scontent.example.net/thumb222.jpg">
</a>
<a href="/marketplace/item/222/?ref=y" aria-label=", 2.000 CHF, Bern, BE, Inserat 222">
  <img src="https://scontent.example.net/thumb222.jpg">
</a>
<a href="/marketplace/item/333/?ref=x" aria-label="Foreign Item, 3.000 EUR, Munich, BY, Inserat 333">
  <img src="https://scontent.example.net/thumb333.jpg">
</a>
</body></html>
"""


def _client_redirect_html(target_url):
    """A same-navigation client-side redirect, used instead of fulfilling
    with an HTTP 3xx status + Location header: Playwright's route
    interception does not reliably re-intercept the redirect target for a
    top-level navigation (confirmed by testing - the follow-up request
    silently escaped to the real network instead), whereas a JS-initiated
    navigation from within an already-controlled page is properly
    intercepted like any other goto()."""
    return f'<html><head><script>window.location.replace("{target_url}");</script></head><body></body></html>'


def default_detail_html(
    listing_id,
    *,
    condition="Neu",
    description="A great item.\nSecond line.",
    posted="vor 2 Tagen",
    location="Zürich, ZH",
    header="Beschreibung durch den Verkäufer",
):
    posted_line = f"Gepostet {posted} – hier: {location}" if posted else f"Gepostet – hier: {location}"
    return f"""
    <html><head><meta charset="utf-8"><title>Cool Item {listing_id} – Facebook Marketplace | Facebook</title></head>
    <body><div role="main">
      <div>Cool Item {listing_id}</div>
      <div>1.000 CHF</div>
      <div>{posted_line}</div>
      <div>{header}</div>
      <div>Zustand</div>
      <div>{condition}</div>
      <div style="white-space: pre-wrap">{description}</div>
      <div> Mehr ansehen</div>
      <div>{location} · Ungefährer Standort wird angezeigt</div>
      <img src="https://scontent.example.net/full_{listing_id}_1.jpg">
      <img src="https://scontent.example.net/full_{listing_id}_2.jpg">
      <h2>Heutige Auswahl</h2>
      <img src="https://scontent.example.net/unrelated_thumb.jpg">
    </div></body></html>
    """


def rental_detail_html(listing_id, *, price="450 CHF/month", location="Baden, AG"):
    """A rental listing's real page shape (confirmed by testing): a category
    link back to "propertyrentals" that plain for-sale listings don't have,
    no condition, and no relative post date - see _extract_category's and
    _PRICE_PERIOD_RE's comments in scraper.py."""
    return f"""
    <html><head><meta charset="utf-8"><title>Cool Rental {listing_id} – Facebook Marketplace | Facebook</title></head>
    <body><div role="main">
      <div>Cool Rental {listing_id}</div>
      <div>{price}</div>
      <a href="/marketplace/109886099040554/propertyrentals/">Property to rent</a>
      <div>Property for rent location</div>
      <div>{location}</div>
      <div>Location is approximate</div>
      <div>Description</div>
      <div>A great rental.</div>
      <img src="https://scontent.example.net/full_{listing_id}_1.jpg">
      <h2>Today's picks</h2>
      <img src="https://scontent.example.net/unrelated_thumb.jpg">
    </div></body></html>
    """


def structural_detail_html(
    listing_id,
    *,
    title,
    price,
    posted,
    description_header,
    description,
    location,
    approx_caption,
    condition_label=None,
    condition_value=None,
    extra_attributes=(),
    toggle_label=None,
    extra_header=None,
    seller_header,
    picks_header,
    seller_name=None,
    seller_id="900000000000001",
    seller_photo_url="https://scontent.example.net/seller_avatar.jpg",
    seller_joined="Joined Facebook in 2020",
    seller_listing_ids=None,
    ad_and_related_searches=False,
):
    """Builds the real DOM shape confirmed by testing against live listings
    with the same account set to English, German and French - h1 for the
    title, an <abbr> for the relative post date, and the description section
    bounded by two <h2> elements (three if `extra_header` mimics a rental
    listing's extra location header) - see _DETAIL_STRUCTURE_JS's big
    comment in scraper.py for why this is read structurally rather than by
    matching any of these words. Every text parameter is free-form on
    purpose: passing the exact same structure with different (even
    unrecognized) wording must still extract the same fields, which is the
    whole point of the structural approach over the legacy word-matching one.

    When `seller_name` is given, also builds the seller-info section (a
    profile link with a real aria-label plus a decoy "Seller details" link,
    an SVG-clipped avatar <image>, and a "Joined Facebook in ..." leaf - the
    exact shape confirmed by testing, see _SELLER_INFO_JS in scraper.py) and
    a hidden `div[role="dialog"]` mimicking Marketplace's own "<name>'s
    listings" popup, wired up with a tiny inline script so clicking the real
    (aria-labelled) profile link reveals it - close enough to the live SPA's
    click-to-open behaviour for _fetch_seller_listing_ids() to be exercised
    without a real Facebook session.

    `ad_and_related_searches=True` adds the "Ad" and "Related searches"
    sections some real pages have between the seller section and the
    related-listings rail (confirmed on real listings in 2026-10), shaped so
    that counting h2s from the end reads the ad's text as the location and
    a related search as the seller's join date.

    `extra_attributes` adds more (label, value) pairs after the condition
    (e.g. ("Colour", "Grey")), and `approx_caption=None` leaves out the
    "location is approximate" caption - both seen on real listings in
    2026-10."""
    condition_html = (
        f"<span><span>{condition_label}</span></span><span><span>{condition_value}</span></span>"
        if condition_label
        else ""
    )
    condition_html += "".join(
        f"<span><span>{label}</span></span><span><span>{value}</span></span>" for label, value in extra_attributes
    )
    approx_caption_html = f"<span><span>{approx_caption}</span></span>" if approx_caption else ""
    toggle_html = f'<div role="button"><span>{toggle_label}</span></div>' if toggle_label else ""
    extra_header_html = f"<h2>{extra_header}</h2>" if extra_header else ""

    seller_section_html = "<span>Seller details</span>"
    dialog_html = ""
    if seller_name:
        profile_href = f"/marketplace/profile/{seller_id}/?product_id={listing_id}"
        seller_section_html = f"""
          <a href="{profile_href}"><span>Seller details</span></a>
          <a aria-label="{seller_name}" href="{profile_href}"><span>{seller_name}</span></a>
          <svg><image xlink:href="{seller_photo_url}"></image></svg>
          <div><span>{seller_joined}</span></div>
        """
        item_links = "".join(
            f'<a href="/marketplace/item/{iid}/?ref=marketplace_profile">item {iid}</a>'
            for iid in (seller_listing_ids or [])
        )
        dialog_html = f"""
        <div role="dialog" style="display:none" id="seller-dialog">
          <h2>About</h2>
          <h2>{seller_name}'s listings</h2>
          {item_links}
        </div>
        <script>
        document.querySelectorAll('a[aria-label="{seller_name}"]').forEach(function (a) {{
            a.addEventListener('click', function (e) {{
                e.preventDefault();
                document.getElementById('seller-dialog').style.display = 'block';
            }});
        }});
        </script>
        """

    trailing_sections_html = ""
    if ad_and_related_searches:
        trailing_sections_html = """
          <h2>Ad</h2>
          <a href="https://ad.example.com/">
            <img src="https://scontent.example.net/ad_banner.jpg">
            <span><span>SWICA</span></span>
            <span><span>ad.example.com</span></span>
          </a>
          <h2>Related searches</h2>
          <a href="/marketplace/zurich/search?query=pixel%2010%20pro"><div><span>google pixel 10 pro</span></div></a>
          <a href="/marketplace/zurich/search?query=iphone%20case"><div><span>iphone case</span></div></a>
        """

    return f"""
    <html><head><meta charset="utf-8"><title>{title}</title></head>
    <body><div role="main">
      <h1>{title}</h1>
      <span>{price}</span>
      <abbr aria-label="{posted}">{posted}</abbr>
      <img src="https://scontent.example.net/full_{listing_id}_1.jpg">
      <img src="https://scontent.example.net/full_{listing_id}_2.jpg">
      {extra_header_html}
      <h2>{description_header}</h2>
      {condition_html}
      <div><span style="white-space: pre-wrap">{description}</span></div>
      {toggle_html}
      <span><span>{location}</span></span>
      {approx_caption_html}
      <h2>{seller_header}</h2>
      {seller_section_html}
      {trailing_sections_html}
      <h2>{picks_header}</h2>
      <img src="https://scontent.example.net/unrelated_thumb.jpg">
    </div>
    {dialog_html}
    </body></html>
    """


def listing_json_html(
    listing_id,
    *,
    title=None,
    description=None,
    attributes=(),
    location_text=None,
    reverse_geocode=None,
    photo_urls=(),
    seller_id=None,
    seller_name=None,
    seller_photo_url=None,
    category_slug=None,
):
    """The <script type="application/json"> blocks a real listing page
    embeds, split up the way the real ones are (confirmed 2026-10, see
    _extract_detail_json() in scraper.py): the photos and the other fields
    in separate records, `location` first with coordinates only and later
    with `reverse_geocode` too, and a plain object (no "__typename") with
    the listing's id whose location is the viewer's search area - here a
    different city, so reading it would show up in the result.
    `attributes` are (attribute_name, value, label) triples."""

    def block(target):
        doc = {
            "require": [
                [
                    "ScheduledServerJS",
                    "handle",
                    None,
                    [
                        {
                            "__bbox": {
                                "result": {"data": {"viewer": {"marketplace_product_details_page": {"target": target}}}}
                            }
                        }
                    ],
                ]
            ]
        }
        return f'<script type="application/json">{json.dumps(doc)}</script>'

    record = {"__typename": "GroupCommerceProductItem", "id": listing_id}
    coords = {"latitude": 46.02, "longitude": 8.97}
    search_area = {
        "id": listing_id,
        "location": {"radius": 500, "reverse_geocode_detailed": {"city": "Zürich", "state": "ZH"}},
        "location_text": {"text": "Zürich, ZH"},
    }
    details = {
        **record,
        "marketplace_listing_title": title,
        "redacted_description": {"text": description} if description is not None else None,
        "attribute_data": [{"attribute_name": n, "value": v, "label": label} for n, v, label in attributes],
        "location_text": {"text": location_text} if location_text else None,
        "location": {**coords, "reverse_geocode_detailed": reverse_geocode} if reverse_geocode else coords,
        "marketplace_listing_category": {"slug": category_slug, "id": "1468271819871448"} if category_slug else None,
        "marketplace_listing_seller": (
            {"__typename": "User", "id": seller_id, "name": seller_name, "profile_picture": {"uri": seller_photo_url}}
            if seller_id
            else None
        ),
    }
    return "".join(
        [
            block(search_area),
            block({**record, "listing_photos": [{"image": {"uri": u}} for u in photo_urls]}),
            block({**record, "location": coords}),
            block(details),
        ]
    )


@pytest.fixture(autouse=True)
def fast_scrolling(request, monkeypatch):
    """Unit tests' fake pages load instantly and never load more results,
    so don't spend real-site waits on them: scroll_to_load() always runs
    until its idle limit, and every search page load waits PAGE_SETTLE_MS.
    e2e tests keep the real timing."""
    if request.node.get_closest_marker("e2e") is None:
        monkeypatch.setattr(scraper, "SCROLL_PAUSE_MS", 10)
        monkeypatch.setattr(scraper, "PAGE_SETTLE_MS", 300)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def mock_context_factory(browser):
    """Returns a factory: mock_context_factory(search_html=..., detail_html_map=...)
    -> a BrowserContext where every request to a Marketplace search URL gets
    `search_html` (or `search_html(url)`, if it's callable) and every
    request to a listing's own page gets
    `detail_html_map[listing_id]` (falling back to default_detail_html).
    Requests to /api/graphql/ get `graphql_bodies` in order, one per request
    (an empty JSON object once they run out) - or `graphql_bodies(request)`,
    if it's callable."""
    contexts = []

    def _make(
        search_html=None,
        detail_html_map=None,
        unmatched="abort",
        login_wall=False,
        consent_wall=False,
        graphql_bodies=None,
    ):
        detail_html_map = detail_html_map or {}
        pending_graphql = [] if callable(graphql_bodies) else list(graphql_bodies or [])

        def handler(route):
            url = route.request.url
            if login_wall and ("/login/" in url or ("/marketplace/" in url and "next" not in url)):
                if "/login/" in url:
                    route.fulfill(
                        status=200, content_type="text/html; charset=utf-8", body="<html><body>login page</body></html>"
                    )
                else:
                    route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body=_client_redirect_html("https://www.facebook.com/login/?next=x"),
                    )
                return
            if consent_wall and ("/privacy/consent/" in url or ("/marketplace/" in url and "flow=" not in url)):
                if "/privacy/consent/" in url:
                    route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body="<html><body>consent page</body></html>",
                    )
                else:
                    route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body=_client_redirect_html("https://www.facebook.com/privacy/consent/?flow=fb_dma_marketplace"),
                    )
                return
            if "/api/graphql" in url:
                if callable(graphql_bodies):
                    body = graphql_bodies(route.request)
                else:
                    body = pending_graphql.pop(0) if pending_graphql else "{}"
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
                return
            item_match = ITEM_ID_RE.search(url)
            if item_match and "/search" not in url:
                listing_id = item_match.group(1)
                html = detail_html_map.get(listing_id) or default_detail_html(listing_id)
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
            elif "/marketplace/" in url and "/search" in url:
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=(search_html(url) if callable(search_html) else search_html) or DEFAULT_SEARCH_HTML,
                )
            elif unmatched == "abort":
                route.abort()
            else:
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<html></html>")

        context = browser.new_context()
        context.route("**/*", handler)
        contexts.append(context)
        return context

    yield _make
    for c in contexts:
        c.close()


@pytest.fixture
def mock_page(mock_context_factory):
    context = mock_context_factory()
    page = context.new_page()
    yield page
    page.close()


class FakeRadiusAccount:
    """A Marketplace search page with a working "Change location" dialog,
    shaped like the real one (confirmed by testing): a location filter
    button whose aria-label ends in "Within <n> km", a dialog with a radius
    combobox opening a listbox of "<n> kilometres" options, and an Apply
    button. Applying saves the radius "on the account" (this object), so
    the next page load reports it in the page data as filter_radius_km -
    pass the instance as `search_html` to mock_context_factory.
    `apply_works=False` makes Apply do nothing."""

    def __init__(
        self,
        radius_km=250,
        offered=(1, 2, 5, 10, 20, 40, 60, 80, 100, 250, 500),
        apply_works=True,
        places=None,
        partial_places=(),
    ):
        self.radius_km = radius_km
        self.offered = offered
        self.apply_works = apply_works
        self.saves = 0
        # location search: full typed text -> [(name, id, lat, lon), ...];
        # any other (partial) text gets `partial_places`
        self.places = places or {}
        self.partial_places = list(partial_places)
        self.search_urls = []

    def graphql(self, request):
        """Answer the location field's typeahead requests like Facebook's
        city_street_search - pass as `graphql_bodies` to mock_context_factory."""
        variables = json.loads(parse_qs(request.post_data or "").get("variables", ["{}"])[0])
        text = variables.get("params", {}).get("query", "")
        edges = [
            {
                "node": {
                    "single_line_address": name,
                    "page": {"id": pid},
                    "location": {"latitude": lat, "longitude": lon},
                }
            }
            for name, pid, lat, lon in self.places.get(text, self.partial_places)
        ]
        return json.dumps({"data": {"city_street_search": {"street_results": {"edges": edges}}}})

    def __call__(self, url):
        saved = re.search(r"__save_radius=(\d+)", url)
        if saved:
            self.saves += 1
            if self.apply_works:
                self.radius_km = int(saved.group(1))
            return "<html></html>"
        self.search_urls.append(url)
        options = "".join(
            f'<div role="option" onclick="window.picked={km}">{km} kilometres</div>' for km in self.offered
        )
        return f"""
        <html><body>
        <script type="application/json">{{"marketplace_filters": {{"filter_radius_km": {self.radius_km}}}}}</script>
        <div role="button" aria-label="Location: Zürich, Switzerland, Within {self.radius_km} km"
             onclick="document.getElementById('dlg').hidden = false">Zürich · Within {self.radius_km} km</div>
        <div role="dialog" id="dlg" hidden>
          <div role="button" aria-label="Close">x</div>
          <input role="combobox" aria-label="Location" value="Zürich, Switzerland"
                 oninput="fetch('/api/graphql/', {{method: 'POST',
                   headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                   body: 'variables=' + encodeURIComponent(JSON.stringify({{params: {{query: this.value}}}}))}})">
          <label role="combobox" aria-haspopup="listbox"
                 onclick="document.getElementById('lb').hidden = false">Radius {self.radius_km} kilometres</label>
          <div role="listbox" id="lb" hidden>{options}</div>
          <div role="button" aria-label="Apply"
               onclick="fetch('/marketplace/zurich/search?__save_radius=' + window.picked)">Apply</div>
        </div>
        </body></html>
        """
