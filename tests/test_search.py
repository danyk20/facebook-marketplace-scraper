import pytest
from bs4 import BeautifulSoup

from fb_scraper import config
from fb_scraper.scraper import (
    LoginRequiredError,
    MarketplaceConsentRequiredError,
    build_search_url,
    parse_tile,
    search_listings,
)


def _anchor(html):
    return BeautifulSoup(html, "lxml").find("a")


def test_parse_tile_with_title():
    item = _anchor(
        '<a href="/marketplace/item/111/?ref=x" '
        'aria-label="Cool Item, 1.000 CHF, Zürich, ZH, Inserat 111">'
        '<img src="https://scontent.example.net/thumb.jpg"></a>'
    )
    result = parse_tile(item)
    assert result == {
        "listing_id": "111",
        "title": "Cool Item",
        "price": "1.000 CHF",
        "location": "Zürich, ZH",
        "url": "https://www.facebook.com/marketplace/item/111/",
        "image_url": "https://scontent.example.net/thumb.jpg",
    }


def test_parse_tile_empty_title_is_none_not_empty_string():
    item = _anchor('<a href="/marketplace/item/111/" aria-label=", 500 CHF, Bern, BE, Inserat 111"></a>')
    result = parse_tile(item)
    assert result["title"] is None
    assert result["price"] == "500 CHF"


def test_parse_tile_title_with_internal_commas():
    item = _anchor(
        '<a href="/marketplace/item/111/" '
        'aria-label="Rare, Special, Item, 99 CHF, Affoltern am Albis, ZH, Inserat 111"></a>'
    )
    result = parse_tile(item)
    assert result["title"] == "Rare, Special, Item"
    assert result["location"] == "Affoltern am Albis, ZH"


def test_parse_tile_non_item_link_returns_none():
    item = _anchor('<a href="/marketplace/you/selling"></a>')
    assert parse_tile(item) is None


def test_parse_tile_falls_back_to_span_text_when_aria_label_missing():
    item = _anchor(
        '<a href="/marketplace/item/111/"><span>Some Title</span><span>50 CHF</span></a>'
    )
    result = parse_tile(item)
    assert result["title"] == "Some Title"


def test_build_search_url_includes_anchor_and_stable_sort():
    url = build_search_url("Tesla Model S")
    assert url.startswith("https://www.facebook.com/marketplace/zurich/search?")
    assert "query=Tesla+Model+S" in url
    assert "sortBy=price_ascend" in url
    assert "radius=500" in url


def test_build_search_url_all_filters():
    url = build_search_url(
        "Tesla", min_price=1000, max_price=2000,
        min_mileage=0, max_mileage=50000,
        min_year=2018, max_year=2020,
        condition=["new", "used_like_new"],
    )
    assert "minPrice=1000" in url
    assert "maxPrice=2000" in url
    assert "minMileage=0" in url
    assert "maxMileage=50000" in url
    assert "minYear=2018" in url
    assert "maxYear=2020" in url
    assert "itemCondition=new%2Cused_like_new" in url


def test_build_search_url_condition_as_plain_string():
    url = build_search_url("Tesla", condition="new")
    assert "itemCondition=new" in url


def test_build_search_url_unknown_country_raises():
    with pytest.raises(ValueError, match="ch"):
        build_search_url("Tesla", country="de")


def test_search_listings_dedupes_and_flags_locality(mock_context_factory):
    context = mock_context_factory()
    page = context.new_page()
    listings = search_listings(page, "Tesla Model S", max_scrolls=1, verbose=False)
    page.close()

    ids = [item["listing_id"] for item in listings]
    assert ids.count("222") == 1, "duplicate hrefs for the same listing id must be de-duplicated"
    assert set(ids) == {"111", "222", "333"}

    by_id = {item["listing_id"]: item for item in listings}
    assert by_id["111"]["is_local"] is True    # Zürich, ZH
    assert by_id["333"]["is_local"] is False   # Munich, BY - not a Swiss canton


def test_search_listings_every_item_has_country(mock_context_factory):
    context = mock_context_factory()
    page = context.new_page()
    listings = search_listings(page, "Tesla Model S", max_scrolls=1, verbose=False)
    page.close()
    assert all(item["country"] == config.DEFAULT_COUNTRY for item in listings)


def test_search_listings_raises_login_required_on_redirect(mock_context_factory):
    context = mock_context_factory(login_wall=True)
    page = context.new_page()
    with pytest.raises(LoginRequiredError, match="search results"):
        search_listings(page, "Tesla Model S", max_scrolls=1, verbose=False)
    page.close()


def test_search_listings_raises_consent_required_on_redirect(mock_context_factory):
    context = mock_context_factory(consent_wall=True)
    page = context.new_page()
    with pytest.raises(MarketplaceConsentRequiredError, match="search results"):
        search_listings(page, "Tesla Model S", max_scrolls=1, verbose=False)
    page.close()
