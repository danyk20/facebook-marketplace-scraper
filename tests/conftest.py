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


def default_detail_html(listing_id, *, condition="Neu", description="A great item.\nSecond line.",
                         posted="vor 2 Tagen", location="Zürich, ZH", header="Beschreibung durch den Verkäufer"):
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
      <div>Heutige Auswahl</div>
      <img src="https://scontent.example.net/unrelated_thumb.jpg">
    </div></body></html>
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

    def _make(search_html=None, detail_html_map=None, unmatched="abort"):
        detail_html_map = detail_html_map or {}

        def handler(route):
            url = route.request.url
            item_match = ITEM_ID_RE.search(url)
            if item_match and "/search" not in url:
                listing_id = item_match.group(1)
                html = detail_html_map.get(listing_id) or default_detail_html(listing_id)
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
            elif "/marketplace/" in url and "/search" in url:
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=search_html or DEFAULT_SEARCH_HTML)
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
