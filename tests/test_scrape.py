import pytest

from fb_scraper.scraper import ScrapeResult, scrape


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
        def __init__(self, headless=True):
            self.headless = headless

        def __enter__(self):
            return context

        def __exit__(self, *exc_info):
            closed["value"] = True

    monkeypatch.setattr("fb_scraper.browser.FacebookSession", _FakeSession)

    result = scrape("Tesla Model S", verbose=False)

    assert result.total_elements == 2
    assert closed["value"] is True


def test_scrape_verbose_prints_local_filter_summary(mock_context_factory, capsys):
    context = mock_context_factory()
    scrape("Tesla Model S", session=context, verbose=True)
    out = capsys.readouterr().out
    assert "kept 2/3 listings" in out
