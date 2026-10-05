import json

import pytest
from bs4 import BeautifulSoup

from fb_scraper import config, scraper
from fb_scraper.scraper import (
    CityNotFoundError,
    LocationNotRecognizedError,
    LoginRequiredError,
    MarketplaceConsentRequiredError,
    SearchRadiusError,
    _collect_search_listing_nodes,
    _json_docs_from_graphql,
    _json_docs_from_html,
    _price_split_point,
    _request_texts,
    account_search_radius,
    build_search_url,
    listing_from_json,
    lookup_city,
    parse_tile,
    search_all_listings,
    search_listings,
    set_account_search_radius,
    supported_radius_km,
)
from tests.conftest import FakeRadiusAccount, _client_redirect_html


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
    assert "sortBy=creation_time_descend" in url
    assert "radius" not in url, "Facebook ignores a URL radius - the account setting decides"


def test_build_search_url_all_filters():
    url = build_search_url(
        "Tesla",
        min_price=1000,
        max_price=2000,
        condition=["new", "used_like_new"],
    )
    assert "minPrice=1000" in url
    assert "maxPrice=2000" in url
    for unsupported in ("minMileage", "maxMileage", "minYear", "maxYear"):
        assert unsupported not in url
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


def test_search_listings_stops_scrolling_when_facebook_says_results_ended(mock_context_factory, monkeypatch):
    """has_next_page: false - from the embedded batch or any scroll batch -
    is what tells scroll_to_load() it's done; empty batches before it with
    has_next_page: true are not the end (both confirmed by testing)."""
    embedded = _search_payload(_listing_node("111"))
    embedded["data"]["marketplace_search"]["feed_units"]["page_info"] = {"has_next_page": True}
    empty_batch = _search_payload()
    empty_batch["data"]["marketplace_search"]["feed_units"]["page_info"] = {"has_next_page": True}
    last_batch = _search_payload(_listing_node("222"))
    last_batch["data"]["marketplace_search"]["feed_units"]["page_info"] = {"has_next_page": False}
    search_html = f"""
    <html><body>
    <script type="application/json">{json.dumps(embedded)}</script>
    <script>
      fetch("/api/graphql/", {{method: "POST"}}).then(() => fetch("/api/graphql/", {{method: "POST"}}));
    </script>
    </body></html>
    """
    seen = {}

    def fake_scroll(page, max_scrolls, *, is_done, progress):
        page.wait_for_timeout(500)  # let both fetches finish
        seen["done"], seen["batches"] = is_done(), progress()

    monkeypatch.setattr(scraper, "scroll_to_load", fake_scroll)
    context = mock_context_factory(
        search_html=search_html, graphql_bodies=[json.dumps(empty_batch), json.dumps(last_batch)]
    )
    page = context.new_page()
    listings = search_listings(page, "Tesla Model X", verbose=False)
    page.close()

    assert seen == {"done": True, "batches": 3}
    assert {item["listing_id"] for item in listings} == {"111", "222"}


def _priced(listing_id, price):
    return {"listing_id": listing_id, "price": f"{price} CHF"}


def test_price_split_point_is_median():
    listings = [_priced(str(i), p) for i, p in enumerate([10, 20, 30, 40, 1000])]
    assert _price_split_point(listings, None, None) == 30


def test_price_split_point_none_when_it_wouldnt_shrink_the_range():
    same_price = [_priced(str(i), 300) for i in range(5)]
    assert _price_split_point(same_price, None, 300) is None  # upper half [301, 300] would be empty
    assert _price_split_point([{"listing_id": "1", "price": None}], None, None) is None


def _inventory(n, max_price=None):
    """`n` listings in posting order (what newest-first returns), with
    prices spread over CHF 1..max_price in no particular order, like real
    listings - every price used exactly once."""
    max_price = max_price or n
    step = 7919  # prime, so i * step % max_price visits every price once
    return [_priced(str(i), i * step % max_price + 1) for i in range(n)]


class _CappedMarketplace:
    """Fake search_listings(): `inventory` is every listing that exists, in
    posting order; each search returns only the first `cap` of those within
    its price range - like Facebook ending big searches early."""

    def __init__(self, inventory, cap):
        self.inventory, self.cap, self.calls = inventory, cap, []

    def __call__(self, page, query, country, *, min_price=None, max_price=None, verbose=True, **kwargs):
        self.calls.append((min_price, max_price))
        lo, hi = min_price or 0, float("inf") if max_price is None else max_price
        in_range = [item for item in self.inventory if lo <= int(item["price"].split()[0]) <= hi]
        return [dict(item) for item in in_range[: self.cap]]


def test_search_all_listings_splits_big_searches_until_everything_is_found(monkeypatch):
    inventory = _inventory(500)
    fake = _CappedMarketplace(inventory, cap=250)
    monkeypatch.setattr(scraper, "search_listings", fake)

    listings = search_all_listings(None, "iPhone 15", verbose=False)

    assert {item["listing_id"] for item in listings} == {item["listing_id"] for item in inventory}
    assert len(listings) == 500, "listings found by several searches must be de-duplicated"
    # first the whole range, then two halves that neither overlap nor leave a gap
    (whole, lower, upper) = fake.calls[0], fake.calls[1], fake.calls[-1]
    assert whole == (None, None)
    assert lower[0] is None and upper[1] is None
    assert any(call[0] == lower[1] + 1 for call in fake.calls)


def test_search_all_listings_does_not_split_small_searches(monkeypatch):
    fake = _CappedMarketplace(_inventory(50), cap=250)
    monkeypatch.setattr(scraper, "search_listings", fake)
    assert len(search_all_listings(None, "Tesla Model X", verbose=False)) == 50
    assert fake.calls == [(None, None)]


def test_search_all_listings_split_disabled(monkeypatch):
    fake = _CappedMarketplace(_inventory(500), cap=250)
    monkeypatch.setattr(scraper, "search_listings", fake)
    assert len(search_all_listings(None, "iPhone 15", split_threshold=None, verbose=False)) == 250
    assert len(fake.calls) == 1


def test_search_all_listings_keeps_user_price_range(monkeypatch):
    fake = _CappedMarketplace(_inventory(1000), cap=250)
    monkeypatch.setattr(scraper, "search_listings", fake)
    listings = search_all_listings(None, "iPhone 15", min_price=100, max_price=600, verbose=False)
    assert len(listings) == 501
    assert all(lo is not None and lo >= 100 and hi is not None and hi <= 600 for lo, hi in fake.calls)


def test_search_all_listings_stops_when_prices_cant_be_split(monkeypatch):
    fake = _CappedMarketplace([_priced(str(i), 300) for i in range(500)], cap=250)
    monkeypatch.setattr(scraper, "search_listings", fake)
    listings = search_all_listings(None, "iPhone 15", verbose=False)
    assert len(listings) == 250  # can't do better: they all cost the same
    assert len(fake.calls) <= 3


def test_search_all_listings_split_depth_is_capped(monkeypatch):
    fake = _CappedMarketplace(_inventory(100_000), cap=250)
    monkeypatch.setattr(scraper, "search_listings", fake)
    search_all_listings(None, "iPhone", verbose=False)
    assert len(fake.calls) == 2 ** (scraper.MAX_SPLIT_DEPTH + 1) - 1


# --- Search radius (account setting) -----------------------------------------


def test_account_search_radius_reads_page_data(mock_context_factory):
    context = mock_context_factory(search_html=FakeRadiusAccount(radius_km=250))
    page = context.new_page()
    page.goto(build_search_url("Tesla"))
    assert account_search_radius(page) == 250
    page.close()


def test_account_search_radius_none_when_page_doesnt_say(mock_page):
    mock_page.goto(build_search_url("Tesla"))
    assert account_search_radius(mock_page) is None


def test_set_account_search_radius_changes_and_verifies(mock_context_factory):
    account = FakeRadiusAccount(radius_km=250)
    page = mock_context_factory(search_html=account).new_page()
    set_account_search_radius(page, 500, "Tesla Model X", verbose=False)
    page.close()
    assert account.radius_km == 500
    assert account.saves == 1


def test_set_account_search_radius_leaves_matching_radius_alone(mock_context_factory):
    account = FakeRadiusAccount(radius_km=500)
    page = mock_context_factory(search_html=account).new_page()
    set_account_search_radius(page, 500, "Tesla Model X", verbose=False)
    page.close()
    assert account.saves == 0, "must not touch the account setting when it's already right"


def test_set_account_search_radius_can_lower_it_too(mock_context_factory):
    account = FakeRadiusAccount(radius_km=500)
    page = mock_context_factory(search_html=account).new_page()
    set_account_search_radius(page, 40, "Tesla Model X", verbose=False)
    page.close()
    assert account.radius_km == 40


def test_set_account_search_radius_rounds_up_to_one_facebook_offers(mock_context_factory):
    account = FakeRadiusAccount(radius_km=250)
    page = mock_context_factory(search_html=account).new_page()
    set_account_search_radius(page, 300, "Tesla", verbose=False)
    page.close()
    assert account.radius_km == 500


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (0.5, 1),
        (1, 1),
        (3, 5),
        (3.5, 5),
        (30, 40),
        (40, 40),
        (100, 100),
        (101, 250),
        (300, 500),
        (500, 500),
        (501, 500),
        (10_000, 500),
    ],
)
def test_supported_radius_km_rounds_up_or_caps_at_max(requested, expected):
    assert supported_radius_km(requested) == expected


@pytest.mark.parametrize("radius", [0, -1, -0.5])
def test_supported_radius_km_rejects_non_positive(radius):
    with pytest.raises(ValueError, match="greater than 0"):
        supported_radius_km(radius)


def test_set_account_search_radius_option_missing_from_dialog(mock_context_factory):
    account = FakeRadiusAccount(radius_km=250, offered=(1, 2, 5, 250))
    page = mock_context_factory(search_html=account).new_page()
    with pytest.raises(SearchRadiusError, match="doesn't offer 500"):
        set_account_search_radius(page, 500, "Tesla", verbose=False)
    page.close()
    assert account.radius_km == 250


def test_set_account_search_radius_detects_change_that_didnt_stick(mock_context_factory):
    account = FakeRadiusAccount(radius_km=250, apply_works=False)
    page = mock_context_factory(search_html=account).new_page()
    with pytest.raises(SearchRadiusError, match="but it's 250"):
        set_account_search_radius(page, 500, "Tesla", verbose=False)
    page.close()


def test_set_account_search_radius_unreadable_radius(mock_page):
    with pytest.raises(SearchRadiusError, match="couldn't read"):
        set_account_search_radius(mock_page, 500, "Tesla", verbose=False)


# --- City lookup ---------------------------------------------------------------

ALTDORF_BAVARIA = ("Altdorf", "111", 48.56, 12.21)
ALTDORF_URI = ("Altdorf, Uri", "222", 46.88, 8.64)
ALTSTAETTEN = ("Altstätten, Switzerland", "999", 47.37, 9.54)


def _city_account(**kwargs):
    account = FakeRadiusAccount(**kwargs)
    return account, {"search_html": account, "graphql_bodies": account.graphql}


def test_lookup_city_takes_first_suggestion_inside_the_country_for_the_full_name(mock_context_factory):
    """Facebook's first suggestion can be in another country (Altdorf in
    Bavaria, confirmed by testing), and partial texts typed on the way get
    their own, different suggestions - neither may be picked."""
    account, fixtures = _city_account(places={"Altdorf": [ALTDORF_BAVARIA, ALTDORF_URI]}, partial_places=[ALTSTAETTEN])
    page = mock_context_factory(**fixtures).new_page()
    assert lookup_city(page, "Altdorf") == ("222", "Altdorf, Uri")
    page.close()
    assert account.saves == 0, "looking up a city must never apply anything to the account"


def test_lookup_city_accepts_a_nearby_place(mock_context_factory):
    account, fixtures = _city_account(places={"Genève": [("Pregny, Geneve, Switzerland", "333", 46.23, 6.14)]})
    page = mock_context_factory(**fixtures).new_page()
    assert lookup_city(page, "Genève") == ("333", "Pregny, Geneve, Switzerland")
    page.close()


def test_lookup_city_nothing_inside_the_country(mock_context_factory):
    account, fixtures = _city_account(places={"Altdorf": [ALTDORF_BAVARIA]})
    page = mock_context_factory(**fixtures).new_page()
    with pytest.raises(CityNotFoundError, match="nothing inside 'ch' for 'Altdorf'.*it suggested: Altdorf"):
        lookup_city(page, "Altdorf")
    page.close()


def test_request_texts_reads_form_and_json_variables():
    body = "fb_api_req_friendly_name=X&variables=" + '{"params":{"query":"Genève","n":5}}'
    assert {"X", "Genève"} <= _request_texts(body)
    assert _request_texts(None) == set()


def test_build_search_url_with_location_id():
    url = build_search_url("Tesla", location="110868505604715")
    assert url.startswith("https://www.facebook.com/marketplace/110868505604715/search?")


def test_search_listings_raises_when_facebook_doesnt_recognise_the_location(mock_context_factory):
    def search_html(url):
        if "/marketplace/geneva/" in url:
            return _client_redirect_html("https://www.facebook.com/marketplace/category/search/?query=x")
        return None  # -> default search page

    page = mock_context_factory(search_html=search_html).new_page()
    with pytest.raises(LocationNotRecognizedError, match="'geneva'"):
        search_listings(page, "Tesla", location="geneva", max_scrolls=1, verbose=False)
    page.close()
