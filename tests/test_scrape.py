import logging

import pytest

from fb_scraper import scraper
from fb_scraper.scraper import ScrapeResult, scrape
from tests.conftest import FakeRadiusAccount


def test_scrape_validates_ranges_before_touching_the_browser(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("scrape() must validate ranges before opening a browser")

    monkeypatch.setattr("fb_scraper.browser.FacebookSession.__enter__", _boom)

    with pytest.raises(ValueError, match="min_price"):
        scrape("Tesla", min_price=100, max_price=50)


def test_scrape_unknown_country_raises_before_touching_the_browser(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("scrape() must validate the country before opening a browser")

    monkeypatch.setattr("fb_scraper.browser.FacebookSession.__enter__", _boom)

    with pytest.raises(ValueError, match="de"):
        scrape("Tesla", country="de")


def test_scrape_end_to_end_with_mocked_context(mock_context_factory):
    context = mock_context_factory()
    result = scrape("Tesla Model S", session=context, verbose=False)

    assert isinstance(result, ScrapeResult)
    assert result.query == "Tesla Model S"
    assert result.country == "ch"
    # 333 (Munich) is filtered out by local_only=True (default)
    assert result.total_elements == 2
    assert len(result.rows) == len(result.listings) == 2
    assert all(row["is_local"] for row in result.rows)


def test_scrape_all_countries_keeps_non_local_listings(mock_context_factory):
    context = mock_context_factory()
    result = scrape("Tesla Model S", session=context, local_only=False, verbose=False)
    assert result.total_elements == 3


def test_scrape_no_detail_skips_detail_fields(mock_context_factory):
    context = mock_context_factory()
    result = scrape("Tesla Model S", session=context, detail=False, verbose=False)
    assert all("condition" not in row for row in result.rows)


def test_scrape_detail_true_adds_condition_and_description(mock_context_factory):
    context = mock_context_factory()
    result = scrape("Tesla Model S", session=context, detail=True, verbose=False)
    assert all(row.get("condition") == "Neu" for row in result.rows)


def test_scrape_rows_sorted_by_price_ascending(mock_context_factory):
    context = mock_context_factory()
    result = scrape("Tesla Model S", session=context, detail=False, verbose=False)
    prices = [row["price"] for row in result.rows]
    assert prices == ["1.000 CHF", "2.000 CHF"]


def test_scrape_reuses_given_session_context(mock_context_factory, monkeypatch):
    """When `session` is given, scrape() must not open its own FacebookSession."""
    context = mock_context_factory()

    def _boom(*a, **kw):
        raise AssertionError("scrape() must not open a FacebookSession when a session is given")

    monkeypatch.setattr("fb_scraper.browser.FacebookSession.__enter__", _boom)

    scrape("Tesla Model S", session=context, verbose=False)


def test_scrape_opens_its_own_session_when_none_given(mock_context_factory, monkeypatch):
    """When `session` is omitted, scrape() must open (and close) a
    FacebookSession itself - faked here so this stays network-free."""
    context = mock_context_factory()
    closed = {"value": False}

    class _FakeSession:
        def __init__(self, headless=True, email=None, password=None):
            self.headless = headless
            self.email = email
            self.password = password

        def __enter__(self):
            return context

        def __exit__(self, *exc_info):
            closed["value"] = True

    monkeypatch.setattr("fb_scraper.browser.FacebookSession", _FakeSession)

    result = scrape("Tesla Model S", verbose=False)

    assert result.total_elements == 2
    assert closed["value"] is True


def test_scrape_forwards_credentials_to_facebook_session(mock_context_factory, monkeypatch):
    context = mock_context_factory()
    captured = {}

    class _FakeSession:
        def __init__(self, headless=True, email=None, password=None):
            captured["email"] = email
            captured["password"] = password

        def __enter__(self):
            return context

        def __exit__(self, *exc_info):
            pass

    monkeypatch.setattr("fb_scraper.browser.FacebookSession", _FakeSession)

    scrape("Tesla Model S", verbose=False, email="test@example.com", password="fake-password-123")

    assert captured["email"] == "test@example.com"
    assert captured["password"] == "fake-password-123"


def test_scrape_verbose_logs_local_filter_summary(mock_context_factory, caplog):
    context = mock_context_factory()
    with caplog.at_level("INFO", logger="fb_scraper.scraper"):
        scrape("Tesla Model S", session=context, verbose=True)
    assert "kept 2/3 listings" in caplog.text


def test_scrape_rejects_radius_facebook_doesnt_offer_before_touching_the_browser(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("scrape() must validate radius_km before opening a browser")

    monkeypatch.setattr("fb_scraper.browser.FacebookSession.__enter__", _boom)
    with pytest.raises(ValueError, match="radius_km"):
        scrape("Tesla", radius_km=300)


def test_scrape_sets_account_radius_to_country_default(mock_context_factory):
    account = FakeRadiusAccount(radius_km=250)
    scrape("Tesla Model S", session=mock_context_factory(search_html=account), detail=False, verbose=False)
    assert account.radius_km == 500  # COUNTRY_ANCHORS["ch"]["radius_km"]


def test_scrape_radius_override(mock_context_factory):
    account = FakeRadiusAccount(radius_km=250)
    context = mock_context_factory(search_html=account)
    scrape("Tesla Model S", session=context, detail=False, radius_km=100, verbose=False)
    assert account.radius_km == 100


def test_scrape_keep_account_radius_never_touches_it(mock_context_factory, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("keep_account_radius=True must not try to change the radius")

    monkeypatch.setattr(scraper, "set_account_search_radius", _boom)
    context = mock_context_factory()
    scrape("Tesla Model S", session=context, detail=False, keep_account_radius=True, verbose=False)


def test_scrape_warns_and_still_searches_if_radius_cant_be_set(mock_context_factory, caplog):
    """The default mock search page has no radius data or location dialog -
    like Facebook having changed its markup. Searching must go ahead anyway."""
    context = mock_context_factory()
    with caplog.at_level(logging.WARNING, logger="fb_scraper"):
        result = scrape("Tesla Model S", session=context, detail=False, verbose=False)
    assert result.total_elements > 0
    assert "couldn't set the search radius to 500 km" in caplog.text


def test_scrape_ignores_year_and_mileage_with_a_warning(mock_context_factory, monkeypatch, caplog):
    """Accepted so existing callers don't break, but never sent to Facebook
    (which would drop every listing without structured vehicle data)."""
    urls = []
    real_build = scraper.build_search_url
    monkeypatch.setattr(scraper, "build_search_url", lambda *a, **kw: urls.append(real_build(*a, **kw)) or urls[-1])
    context = mock_context_factory()
    with caplog.at_level(logging.WARNING, logger="fb_scraper"):
        result = scrape(
            "Tesla Model X",
            session=context,
            detail=False,
            keep_account_radius=True,
            min_year=2015,
            max_year=2017,
            min_mileage=0,
            max_mileage=100_000,
            verbose=False,
        )
    assert result.total_elements > 0
    assert "Ignoring min_year=2015, max_year=2017, min_mileage=0, max_mileage=100000" in caplog.text
    assert urls and not any(p in u for u in urls for p in ("Year", "Mileage"))


def test_scrape_reversed_year_range_is_ignored_not_an_error(mock_context_factory):
    context = mock_context_factory()
    scrape(
        "Tesla", session=context, detail=False, keep_account_radius=True, min_year=2020, max_year=2010, verbose=False
    )


def test_scrape_city_searches_around_the_looked_up_location(mock_context_factory):
    account = FakeRadiusAccount(
        radius_km=500, places={"Altdorf": [("Altdorf", "111", 48.56, 12.21), ("Altdorf, Uri", "222", 46.88, 8.64)]}
    )
    context = mock_context_factory(search_html=account, graphql_bodies=account.graphql)
    scrape("Tesla", session=context, detail=False, city="Altdorf", verbose=False)
    searched = [u for u in account.search_urls if "query=Tesla" in u]
    assert searched and all("/marketplace/222/search" in u for u in searched)


def test_scrape_numeric_city_is_used_as_is(mock_context_factory, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("a numeric city id needs no lookup")

    monkeypatch.setattr(scraper, "lookup_city", _boom)
    account = FakeRadiusAccount(radius_km=500)
    context = mock_context_factory(search_html=account)
    scrape("Tesla", session=context, detail=False, city="110868505604715", verbose=False)
    assert any("/marketplace/110868505604715/search" in u for u in account.search_urls)
