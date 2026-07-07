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

import re

import pytest
from playwright.sync_api import sync_playwright

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
    toggle_label=None,
    extra_header=None,
    seller_header,
    picks_header,
    seller_name=None,
    seller_id="900000000000001",
    seller_photo_url="https://scontent.example.net/seller_avatar.jpg",
    seller_joined="Joined Facebook in 2020",
    seller_listing_ids=None,
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
    without a real Facebook session."""
    condition_html = (
        f"<span><span>{condition_label}</span></span><span><span>{condition_value}</span></span>"
        if condition_label
        else ""
    )
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
      <span><span>{approx_caption}</span></span>
      <h2>{seller_header}</h2>
      {seller_section_html}
      <h2>{picks_header}</h2>
      <img src="https://scontent.example.net/unrelated_thumb.jpg">
    </div>
    {dialog_html}
    </body></html>
    """


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
    `search_html` and every request to a listing's own page gets
    `detail_html_map[listing_id]` (falling back to default_detail_html)."""
    contexts = []

    def _make(search_html=None, detail_html_map=None, unmatched="abort", login_wall=False, consent_wall=False):
        detail_html_map = detail_html_map or {}

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
            item_match = ITEM_ID_RE.search(url)
            if item_match and "/search" not in url:
                listing_id = item_match.group(1)
                html = detail_html_map.get(listing_id) or default_detail_html(listing_id)
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
            elif "/marketplace/" in url and "/search" in url:
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=search_html or DEFAULT_SEARCH_HTML,
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
