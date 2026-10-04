import json

import pytest
from bs4 import BeautifulSoup

from fb_scraper import config
from fb_scraper.scraper import (
    LoginRequiredError,
    MarketplaceConsentRequiredError,
    _collect_search_listing_nodes,
    _json_docs_from_graphql,
    _json_docs_from_html,
    build_search_url,
    listing_from_json,
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
        "original_price": None,
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


def test_parse_tile_english_price_format():
    """Authenticated sessions render in the account's own saved Facebook UI
    language, not the browser locale (confirmed by testing) - English uses a
    currency-prefix, comma-thousands price like "CHF74,900" instead of
    German's digit-first, period-thousands "74.900 CHF". The internal comma
    must not be mistaken for the field separator (see ARIA_RE docstring)."""
    item = _anchor(
        '<a href="/marketplace/item/111/?ref=x" '
        'aria-label="Tesla Model S, CHF74,900, Winterthur, ZH, listing 111">'
        '<img src="https://scontent.example.net/thumb.jpg"></a>'
    )
    result = parse_tile(item)
    assert result["title"] == "Tesla Model S"
    assert result["price"] == "CHF74,900"
    assert result["location"] == "Winterthur, ZH"


def test_parse_tile_english_price_format_no_comma():
    item = _anchor('<a href="/marketplace/item/111/" aria-label=", CHF420, Andwil, SG, listing 111"></a>')
    result = parse_tile(item)
    assert result["title"] is None
    assert result["price"] == "CHF420"
    assert result["location"] == "Andwil, SG"


def test_parse_tile_reduced_price_english():
    """A lowered price adds a "reduced from ..." field after the price (real
    aria-label, confirmed by testing). It must not shift city/region one
    place along - that used to make is_local() drop the listing."""
    item = _anchor(
        '<a href="/marketplace/item/35479156145063974/" aria-label="Original 22&quot; Tesla Model X Onyx Black '
        "Turbine Kompletträder (Sommer), CHF1,900, reduced from CHF2,990, Oberengstringen, ZH, "
        'listing 35479156145063974"></a>'
    )
    result = parse_tile(item)
    assert result["title"] == 'Original 22" Tesla Model X Onyx Black Turbine Kompletträder (Sommer)'
    assert result["price"] == "CHF1,900"
    assert result["original_price"] == "CHF2,990"
    assert result["location"] == "Oberengstringen, ZH"
    assert config.is_local(result["location"], "ch") is True


def test_parse_tile_reduced_price_german():
    item = _anchor(
        '<a href="/marketplace/item/2865696953776182/" aria-label="Tesla FSD Modul / FSD Hack für Model S/X HW3, '
        '170 CHF, reduziert von ursprünglich 300 CHF, Schlieren, ZH, Inserat 2865696953776182"></a>'
    )
    result = parse_tile(item)
    assert result["title"] == "Tesla FSD Modul / FSD Hack für Model S/X HW3"
    assert result["price"] == "170 CHF"
    assert result["original_price"] == "300 CHF"
    assert result["location"] == "Schlieren, ZH"


def test_parse_tile_reduced_price_without_title():
    item = _anchor(
        '<a href="/marketplace/item/111/" '
        'aria-label=", CHF36,999, reduced from CHF38,999, Zürich, ZH, listing 111"></a>'
    )
    result = parse_tile(item)
    assert result["title"] is None
    assert result["price"] == "CHF36,999"
    assert result["original_price"] == "CHF38,999"
    assert result["location"] == "Zürich, ZH"


def test_parse_tile_falls_back_to_span_text_when_aria_label_missing():
    item = _anchor('<a href="/marketplace/item/111/"><span>Some Title</span><span>50 CHF</span></a>')
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
        "Tesla",
        min_price=1000,
        max_price=2000,
        min_mileage=0,
        max_mileage=50000,
        min_year=2018,
        max_year=2020,
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
    assert by_id["111"]["is_local"] is True  # Zürich, ZH
    assert by_id["333"]["is_local"] is False  # Munich, BY - not a Swiss canton


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


# --- Search results from Facebook's own JSON ---------------------------------


def _listing_node(listing_id, *, title="Cool Item", price="CHF1,000", was=None, city="Zürich", state="ZH"):
    """One listing object in the shape Facebook's search JSON uses (trimmed
    from a real response - see listing_from_json())."""
    return {
        "__typename": "GroupCommerceProductItem",
        "id": listing_id,
        "primary_listing_photo": {"image": {"uri": f"https://scontent.example.net/{listing_id}.jpg"}},
        "listing_price": {"formatted_amount": price, "amount": "1000.00"},
        "strikethrough_price": {"formatted_amount": was, "amount": "1200.00"} if was else None,
        "location": {"reverse_geocode": {"city": city, "state": state}},
        "marketplace_listing_title": title,
    }


def _search_payload(*nodes):
    return {"data": {"marketplace_search": {"feed_units": {"edges": [{"node": {"listing": n}} for n in nodes]}}}}


def test_listing_from_json_reduced_price():
    node = _listing_node("111", title="Tesla Model X", price="CHF1,900", was="CHF2,990", city="Schlieren")
    assert listing_from_json(node) == {
        "listing_id": "111",
        "title": "Tesla Model X",
        "price": "CHF1,900",
        "original_price": "CHF2,990",
        "location": "Schlieren, ZH",
        "url": "https://www.facebook.com/marketplace/item/111/",
        "image_url": "https://scontent.example.net/111.jpg",
    }


def test_listing_from_json_missing_optional_fields():
    result = listing_from_json({"id": "111", "listing_price": {"formatted_amount": "500 CHF"}})
    assert result["title"] is None
    assert result["price"] == "500 CHF"
    assert result["original_price"] is None
    assert result["location"] is None
    assert result["image_url"] is None


def test_listing_from_json_location_without_state_uses_city_page_name():
    node = _listing_node("111")
    city_page = {"display_name": "Zürich, Switzerland"}
    node["location"] = {"reverse_geocode": {"city": "Zürich", "state": None, "city_page": city_page}}
    result = listing_from_json(node)
    assert result["location"] == "Zürich, Switzerland"
    assert config.is_local(result["location"], "ch") is True


def test_json_docs_from_graphql_handles_prefix_and_streamed_lines():
    body = "for (;;);" + json.dumps({"a": 1}) + "\n\nnot json\n" + json.dumps({"b": 2})
    assert _json_docs_from_graphql(body) == [{"a": 1}, {"b": 2}]


def test_json_docs_from_html_only_reads_search_blocks():
    html = (
        '<script type="application/json">{"unrelated": 1}</script>'
        f'<script type="application/json" data-x="y">{json.dumps(_search_payload(_listing_node("111")))}</script>'
        '<script type="application/json">{"marketplace_search": broken</script>'
    )
    docs = _json_docs_from_html(html)
    assert len(docs) == 1
    assert "marketplace_search" in docs[0]["data"]


def test_collect_search_listing_nodes_ignores_listings_outside_search():
    doc = {
        "data": {
            "marketplace_search": {"feed_units": {"edges": [{"node": {"listing": _listing_node("111")}}]}},
            "marketplace_feed": {"edges": [{"node": {"listing": _listing_node("999")}}]},
        }
    }
    out = {}
    _collect_search_listing_nodes(doc, out)
    assert list(out) == ["111"]


def test_search_listings_collects_embedded_and_scroll_batches(mock_context_factory):
    """Mirrors what the live site does (confirmed by testing): the first
    batch is embedded in the search page's HTML, later batches arrive from
    /api/graphql/, and tiles scrolled past are no longer in the DOM - so
    listings with no tile left (222, 333) must still be found. A tile with
    no JSON behind it (444) is still picked up as a fallback, and JSON wins
    over a tile's aria-label for the same listing (111)."""
    reduced = _listing_node("111", price="CHF170", was="CHF300", city="Schlieren")
    embedded = _search_payload(reduced, _listing_node("222"))
    search_html = f"""
    <html><body>
    <script type="application/json">{json.dumps(embedded)}</script>
    <script>fetch("/api/graphql/", {{method: "POST", body: "q=1"}});</script>
    <a href="/marketplace/item/111/"
       aria-label="Tile Title, CHF170, reduced from CHF300, Schlieren, ZH, listing 111"></a>
    <a href="/marketplace/item/444/" aria-label="Tile Only, 50 CHF, Bern, BE, Inserat 444"></a>
    </body></html>
    """
    graphql = "for (;;);" + json.dumps(_search_payload(_listing_node("333", city="Munich", state="BY")))
    context = mock_context_factory(search_html=search_html, graphql_bodies=[graphql])
    page = context.new_page()
    listings = search_listings(page, "Tesla Model X", max_scrolls=1, verbose=False)
    page.close()

    by_id = {item["listing_id"]: item for item in listings}
    assert set(by_id) == {"111", "222", "333", "444"}
    assert by_id["111"]["title"] == "Cool Item"  # from JSON, not the tile's aria-label
    assert by_id["111"]["original_price"] == "CHF300"
    assert by_id["111"]["is_local"] is True
    assert by_id["333"]["is_local"] is False
    assert by_id["444"]["title"] == "Tile Only"
    assert all(item["country"] == "ch" for item in listings)
