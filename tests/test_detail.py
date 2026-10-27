import pytest

from fb_scraper.scraper import (
    LoginRequiredError,
    _check_detail,
    _parse_detail_text,
    fetch_detail,
    visit_all_listings,
)
from tests.conftest import default_detail_html, listing_json_html, rental_detail_html, structural_detail_html

# Real wording confirmed by testing (switching the same account between
# languages and re-reading the same real listings) - used below to prove
# the structural extraction gets identical results regardless of which of
# these three it sees, since it never looks at the words themselves. See
# _DETAIL_STRUCTURE_JS's docstring in scraper.py.
_LANG_WORDING = {
    "en": {
        "description_header": "Seller's description",
        "toggle_label": "See translation",
        "approx_caption": "Location is approximate",
        "seller_header": "Seller information",
        "picks_header": "Today's picks",
        "condition_label": "Condition",
        "posted": "5 weeks ago",
    },
    "de": {
        "description_header": "Beschreibung durch den Verkäufer",
        "toggle_label": "Übersetzung anzeigen",
        "approx_caption": "Ungefährer Standort wird angezeigt",
        "seller_header": "Informationen zum Verkäufer",
        "picks_header": "Heutige Auswahl",
        "condition_label": "Zustand",
        "posted": "vor 5 Wochen",
    },
    "fr": {
        "description_header": "Description fournie par le ou la vendeur(se)",
        "toggle_label": "Voir la traduction",
        "approx_caption": "La localisation est approximative",
        "seller_header": "Informations vendeur(se)",
        "picks_header": "Sélection du jour",
        "condition_label": "État",
        "posted": "il y a 5 semaines",
    },
}


def test_parse_detail_text_full_fields():
    html_text = (
        "Cool Item\n1.000 CHF\nGepostet vor 2 Tagen – hier: Zürich, ZH\n"
        "Beschreibung durch den Verkäufer\nZustand\nGebraucht – wie neu\n"
        "Line one.\nLine two.\n Mehr ansehen\nZürich, ZH · Ungefährer Standort wird angezeigt"
    )
    result = _parse_detail_text(html_text)
    assert result["condition"] == "Gebraucht – wie neu"
    assert result["description"] == "Line one.\nLine two."
    assert result["posted_at"] == "vor 2 Tagen"
    assert result["location"] == "Zürich, ZH"


def test_parse_detail_text_handles_details_header_instead_of_beschreibung():
    """Business/rental listings use a "Details" header instead of
    "Beschreibung durch den Verkäufer" - description parsing must not
    depend on which one is present (see module docstring)."""
    html_text = (
        "Rental Car\n99 CHF\nGepostet – hier: Menznau, LU\n"
        "Details\nZustand\nGebraucht – relativ guter Zustand\n"
        "Some rental terms.\n Mehr ansehen\nMenznau, LU · Ungefährer Standort wird angezeigt"
    )
    result = _parse_detail_text(html_text)
    assert result["condition"] == "Gebraucht – relativ guter Zustand"
    assert result["description"] == "Some rental terms."


def test_parse_detail_text_no_relative_post_time_is_none_not_empty():
    html_text = "Item\n99 CHF\nGepostet – hier: Menznau, LU\nZustand\nNeu\nDesc.\n Mehr ansehen"
    result = _parse_detail_text(html_text)
    assert result["posted_at"] is None
    assert result["location"] == "Menznau, LU"


def test_parse_detail_text_missing_zustand_is_defensive():
    result = _parse_detail_text("Just some random page text with no structure.")
    assert result == {"condition": None, "description": None, "posted_at": None, "location": None}


def test_parse_detail_text_english_session_no_condition_field():
    """Authenticated sessions render in the account's own saved Facebook UI
    language (confirmed by testing, not the browser locale) - this is a real
    listing's English rendering, which also has no condition/"Zustand" field
    set at all (common for non-vehicle listings), so description extraction
    must not depend on finding one first (see module docstring)."""
    html_text = (
        "19 Zoll Felgen\nCHF420\nListed 23 weeks ago in Andwil, SG\nMessage\n"
        "Seller's description\n19zoll felgen mit Reiffen.\n5x112\n"
        "2 Reiffen sin fast neu \n2 nicht mehr so gut\nSee translation\n"
        "Andwil, SG\nLocation is approximate\nSeller information"
    )
    result = _parse_detail_text(html_text)
    assert result["condition"] is None
    assert result["description"] == "19zoll felgen mit Reiffen.\n5x112\n2 Reiffen sin fast neu\n2 nicht mehr so gut"
    assert result["posted_at"] == "23 weeks ago"
    assert result["location"] == "Andwil, SG"


def test_parse_detail_text_english_session_with_condition():
    html_text = (
        "Item\nCHF50\nListed in Zurich, ZH\nMessage\n"
        "Seller's description\nCondition\nUsed - good\nGreat item, barely used.\nMessage Seller"
    )
    result = _parse_detail_text(html_text)
    assert result["condition"] == "Used - good"
    assert result["description"] == "Great item, barely used."
    assert result["posted_at"] is None
    assert result["location"] == "Zurich, ZH"


def test_fetch_detail_extracts_everything(mock_context_factory):
    context = mock_context_factory()
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["condition"] == "Neu"
    assert detail["description"] == "A great item.\nSecond line."
    assert detail["posted_at"] == "vor 2 Tagen"
    assert detail["title"] == "Cool Item 111"


def test_fetch_detail_images_exclude_related_listings_rail(mock_context_factory):
    context = mock_context_factory()
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["images"] == [
        "https://scontent.example.net/full_111_1.jpg",
        "https://scontent.example.net/full_111_2.jpg",
    ]
    assert "https://scontent.example.net/unrelated_thumb.jpg" not in detail["images"]


def test_visit_all_listings_backfills_missing_title(mock_context_factory):
    context = mock_context_factory()
    page = context.new_page()
    listings = [
        {
            "listing_id": "111",
            "title": None,
            "price": "1.000 CHF",
            "location": "Zürich, ZH",
            "url": "x",
            "image_url": None,
        }
    ]
    visited = visit_all_listings(page, listings, delay=0, verbose=False)
    page.close()

    assert visited[0]["title"] == "Cool Item 111"  # backfilled from the detail page's <title>
    assert visited[0]["condition"] == "Neu"


def test_visit_all_listings_keeps_tile_title_over_detail_title(mock_context_factory):
    context = mock_context_factory()
    page = context.new_page()
    listings = [
        {
            "listing_id": "111",
            "title": "Tile Title Wins",
            "price": "1.000 CHF",
            "location": "Zürich, ZH",
            "url": "x",
            "image_url": None,
        }
    ]
    visited = visit_all_listings(page, listings, delay=0, verbose=False)
    page.close()

    assert visited[0]["title"] == "Tile Title Wins"


def test_visit_all_listings_handles_condition_variants(mock_context_factory):
    detail_map = {"111": default_detail_html("111", condition="Gebraucht – guter Zustand")}
    context = mock_context_factory(detail_html_map=detail_map)
    page = context.new_page()
    listings = [{"listing_id": "111", "title": "T", "price": "1 CHF", "location": None, "url": "x", "image_url": None}]
    visited = visit_all_listings(page, listings, delay=0, verbose=False)
    page.close()
    assert visited[0]["condition"] == "Gebraucht – guter Zustand"


def test_fetch_detail_strips_english_marketplace_title_prefix(mock_context_factory):
    """The detail page's own <title> - used to backfill a tile with no
    free-text title - is wrapped differently per language (confirmed by
    testing): German is "<item> – Facebook Marketplace | Facebook" (suffix,
    covered by test_fetch_detail_extracts_everything's "Cool Item 111"),
    English is "Marketplace – <item> | Facebook" (prefix)."""
    html = """
    <html><head><meta charset="utf-8"><title>Marketplace – 19 Zoll Felgen | Facebook</title></head>
    <body><div role="main"><div>19 Zoll Felgen</div></div></body></html>
    """
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()
    assert detail["title"] == "19 Zoll Felgen"


def test_fetch_detail_identifies_rental_listing(mock_context_factory):
    detail_map = {"111": rental_detail_html("111")}
    context = mock_context_factory(detail_html_map=detail_map)
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["category"] == "propertyrentals"
    assert detail["is_rental"] is True
    assert detail["price_period"] == "month"
    assert detail["condition"] is None
    assert detail["posted_at"] is None
    assert detail["description"] == "A great rental."


def test_fetch_detail_non_rental_has_no_category(mock_context_factory):
    context = mock_context_factory()
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["category"] is None
    assert detail["is_rental"] is False
    assert detail["price_period"] is None


def test_visit_all_listings_merges_rental_fields(mock_context_factory):
    detail_map = {"111": rental_detail_html("111")}
    context = mock_context_factory(detail_html_map=detail_map)
    page = context.new_page()
    listings = [
        {"listing_id": "111", "title": "T", "price": "450 CHF", "location": None, "url": "x", "image_url": None}
    ]
    visited = visit_all_listings(page, listings, delay=0, verbose=False)
    page.close()

    assert visited[0]["is_rental"] is True
    assert visited[0]["price_period"] == "month"
    assert visited[0]["category"] == "propertyrentals"


@pytest.mark.parametrize("lang", ["en", "de", "fr"])
def test_fetch_detail_structural_same_result_regardless_of_language(mock_context_factory, lang):
    """The core claim of the structural rewrite: identical HTML shape, only
    the wording swapped for each language's real confirmed words, must
    yield identical extracted fields - because none of the extraction looks
    at the wording. See _DETAIL_STRUCTURE_JS's docstring in scraper.py for
    why (DOM shape instead of translated text) and its "tested against
    English/German/French, expected to generalize further" scope note."""
    w = _LANG_WORDING[lang]
    html = structural_detail_html(
        "111",
        title="Cool Item",
        price="CHF50",
        posted=w["posted"],
        description_header=w["description_header"],
        description="A great item.\nSecond line.",
        location="Bern, BE",
        approx_caption=w["approx_caption"],
        condition_label=w["condition_label"],
        condition_value="Used - like new",
        toggle_label=w["toggle_label"],
        seller_header=w["seller_header"],
        picks_header=w["picks_header"],
    )
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["title"] == "Cool Item"
    assert detail["condition"] == "Used - like new"
    assert detail["description"] == "A great item.\nSecond line."
    assert detail["posted_at"] == w["posted"]
    assert detail["images"] == [
        "https://scontent.example.net/full_111_1.jpg",
        "https://scontent.example.net/full_111_2.jpg",
    ]
    assert "https://scontent.example.net/unrelated_thumb.jpg" not in detail["images"]


@pytest.mark.parametrize("lang", ["en", "de", "fr"])
def test_fetch_detail_structural_no_condition_across_languages(mock_context_factory, lang):
    """A listing with no condition set at all (common - see module docstring
    in scraper.py) must still get its description, in every language, not
    just the one(s) this was originally noticed and fixed for."""
    w = _LANG_WORDING[lang]
    html = structural_detail_html(
        "111",
        title="19 Zoll Felgen",
        price="CHF420",
        posted=w["posted"],
        description_header=w["description_header"],
        description="19zoll felgen mit Reiffen.\n5x112",
        location="Andwil, SG",
        approx_caption=w["approx_caption"],
        toggle_label=w["toggle_label"],
        seller_header=w["seller_header"],
        picks_header=w["picks_header"],
    )
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["condition"] is None
    assert detail["description"] == "19zoll felgen mit Reiffen.\n5x112"


def test_fetch_detail_structural_rental_extra_header_before_description(mock_context_factory):
    """A rental listing's page inserts an extra h2 ("Property for rent
    location") before the description header, which a "first two h2s"
    boundary rule would get wrong. Counting from the end instead (see
    _DETAIL_STRUCTURE_JS) must still land on the right one."""
    w = _LANG_WORDING["en"]
    html = structural_detail_html(
        "111",
        title="Tesla Model 3 SR Plus",
        price="CHF450/month",
        posted="",
        description_header="Description",
        description="A great rental.",
        location="Baden, AG",
        approx_caption=w["approx_caption"],
        extra_header="Property for rent location",
        seller_header=w["seller_header"],
        picks_header=w["picks_header"],
    )
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["description"] == "A great rental."
    assert detail["condition"] is None


def test_fetch_detail_raises_login_required_on_redirect(mock_context_factory):
    context = mock_context_factory(login_wall=True)
    page = context.new_page()
    with pytest.raises(LoginRequiredError, match="111"):
        fetch_detail(page, "111")
    page.close()


# --- Seller info -------------------------------------------------------------
#
# Verified against real Facebook Marketplace listings (a solo seller with
# one item, a small private seller with a handful, and a dealer account with
# dozens) before writing these - see _SELLER_INFO_JS's and
# _fetch_seller_listing_ids()'s docstrings in scraper.py for what was
# confirmed live. These tests exercise the same code path against
# structural_detail_html's synthetic seller section/dialog instead, so they
# run without a real Facebook session.


def _seller_html(seller_listing_ids):
    w = _LANG_WORDING["en"]
    return structural_detail_html(
        "111",
        title="Cool Item",
        price="CHF50",
        posted=w["posted"],
        description_header=w["description_header"],
        description="A great item.",
        location="Bern, BE",
        approx_caption=w["approx_caption"],
        seller_header=w["seller_header"],
        picks_header=w["picks_header"],
        seller_name="Jane Seller",
        seller_id="900000000000123",
        seller_photo_url="https://scontent.example.net/jane_avatar.jpg",
        seller_joined="Joined Facebook in 2019",
        seller_listing_ids=seller_listing_ids,
    )


def test_fetch_detail_extracts_seller_name_photo_and_joined(mock_context_factory):
    html = _seller_html(["111"])
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["seller_name"] == "Jane Seller"
    assert detail["seller_profile_url"] == "https://www.facebook.com/marketplace/profile/900000000000123/"
    assert detail["seller_photo_url"] == "https://scontent.example.net/jane_avatar.jpg"
    assert detail["seller_joined"] == "Joined Facebook in 2019"


def test_fetch_detail_counts_and_links_sellers_other_listings(mock_context_factory):
    html = _seller_html(["111", "222", "333"])
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["seller_listing_count"] == 3
    assert detail["seller_listing_urls"] == [
        "https://www.facebook.com/marketplace/item/111/",
        "https://www.facebook.com/marketplace/item/222/",
        "https://www.facebook.com/marketplace/item/333/",
    ]


def test_fetch_detail_seller_listing_count_none_when_dialog_unavailable(mock_context_factory):
    """No seller name/link at all (default_detail_html has no h2 layout,
    same "matched: false" case _extract_seller_info() shares with the main
    structural extraction) - seller fields must stay None/[] rather than
    raising or guessing."""
    context = mock_context_factory()
    page = context.new_page()
    detail = fetch_detail(page, "111")
    page.close()

    assert detail["seller_name"] is None
    assert detail["seller_profile_url"] is None
    assert detail["seller_photo_url"] is None
    assert detail["seller_joined"] is None
    assert detail["seller_listing_count"] is None
    assert detail["seller_listing_urls"] == []


def test_fetch_detail_skips_seller_listings_dialog_when_disabled(mock_context_factory):
    """fetch_seller_listings=False must still populate name/photo/joined
    (already on the page - no extra click needed) but skip the dialog click
    entirely, leaving count/urls at their "unknown" defaults."""
    html = _seller_html(["111", "222"])
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    detail = fetch_detail(page, "111", fetch_seller_listings=False)
    page.close()

    assert detail["seller_name"] == "Jane Seller"
    assert detail["seller_listing_count"] is None
    assert detail["seller_listing_urls"] == []


def test_visit_all_listings_merges_seller_fields(mock_context_factory):
    html = _seller_html(["111", "222"])
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    listings = [{"listing_id": "111", "title": "T", "price": "1 CHF", "location": None, "url": "x", "image_url": None}]
    visited = visit_all_listings(page, listings, delay=0, verbose=False)
    page.close()

    assert visited[0]["seller_name"] == "Jane Seller"
    assert visited[0]["seller_listing_count"] == 2
    assert visited[0]["seller_listing_urls"] == [
        "https://www.facebook.com/marketplace/item/111/",
        "https://www.facebook.com/marketplace/item/222/",
    ]


# --- "Ad" and "Related searches" sections ------------------------------------
#
# Some real pages (confirmed in 2026-10) add these two h2 sections between
# the seller section and the related-listings rail, which broke counting h2s
# from the end of the page - see _DETAIL_STRUCTURE_JS's big comment.
# The fixture also copies the rest of those pages' description section: an
# extra "Colour" label/value pair and no "location is approximate" caption.


def _ad_page_html(*, seller_name="Jane Seller", extra_header=None):
    w = _LANG_WORDING["en"]
    return structural_detail_html(
        "111",
        title="Pixel 10 Pro case",
        price="CHF10",
        posted=w["posted"],
        description_header="Details",
        description="Grey case, looks like new.",
        location="Volketswil, ZH",
        approx_caption=None,
        condition_label=w["condition_label"],
        condition_value="New",
        extra_attributes=[("Colour", "Grey")],
        toggle_label=w["toggle_label"],
        extra_header=extra_header,
        seller_header=w["seller_header"],
        picks_header=w["picks_header"],
        seller_name=seller_name,
        seller_joined="Joined Facebook in 2023",
        ad_and_related_searches=True,
    )


def test_fetch_detail_ad_and_related_searches_sections(mock_context_factory):
    context = mock_context_factory(detail_html_map={"111": _ad_page_html()})
    page = context.new_page()
    detail = fetch_detail(page, "111", fetch_seller_listings=False)
    page.close()

    assert detail["description"] == "Grey case, looks like new."
    assert detail["condition"] == "New"
    assert detail["location"] == "Volketswil, ZH"
    assert detail["seller_name"] == "Jane Seller"
    assert detail["seller_joined"] == "Joined Facebook in 2023"
    assert detail["seller_photo_url"] == "https://scontent.example.net/seller_avatar.jpg"
    assert "https://scontent.example.net/ad_banner.jpg" not in detail["images"]


def test_fetch_detail_ad_sections_on_rental_layout(mock_context_factory):
    """The rental listing's extra h2 comes before the description header,
    so anchoring on the seller's profile link still finds the right one."""
    html = _ad_page_html(extra_header="Property for rent location")
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    detail = fetch_detail(page, "111", fetch_seller_listings=False)
    page.close()

    assert detail["description"] == "Grey case, looks like new."
    assert detail["location"] == "Volketswil, ZH"
    assert detail["seller_name"] == "Jane Seller"


def test_fetch_detail_location_only_structural_result_falls_back(mock_context_factory):
    """Without a profile link to anchor on, counting from the end reads the
    ad as the description section, which yields only a junk location. That
    alone mustn't count as a structural match: fetch_detail() falls back to
    _parse_detail_text() instead of returning the ad's text."""
    context = mock_context_factory(detail_html_map={"111": _ad_page_html(seller_name=None)})
    page = context.new_page()
    detail = fetch_detail(page, "111", fetch_seller_listings=False)
    page.close()

    assert detail["location"] != "SWICA"
    # The fixture's condition label/value are adjacent inline spans, so the
    # page text reads "ConditionNew" on one line ahead of the description.
    assert detail["description"].endswith("Grey case, looks like new.")


# --- Embedded JSON -----------------------------------------------------------
#
# Field names and the way records are split across blocks are from real
# listing pages (2026-10) - see _extract_detail_json() in scraper.py.


def _with_json(html, **json_fields):
    return html.replace("</body>", listing_json_html("111", **json_fields) + "</body>")


def _fetch(mock_context_factory, html):
    context = mock_context_factory(detail_html_map={"111": html})
    page = context.new_page()
    detail = fetch_detail(page, "111", fetch_seller_listings=False)
    page.close()
    return detail


def test_fetch_detail_prefers_embedded_json_over_page(mock_context_factory):
    """Where the JSON and the rendered page disagree, the JSON wins: it's
    Facebook's own data, while the page can be misread (the page here is
    the "Ad"/"Related searches" layout, read correctly - the values differ
    only to show which source was used)."""
    html = _with_json(
        _ad_page_html(),
        title="JSON title",
        description="JSON description.\nSecond line.",
        attributes=[("Condition", "used_good", "Used – good")],
        location_text="Lugano, TI",
        photo_urls=["https://scontent.example.net/json_1.jpg", "https://scontent.example.net/json_2.jpg"],
        seller_id="900000000000777",
        seller_name="Json Seller",
        seller_photo_url="https://scontent.example.net/json_avatar.jpg",
    )
    detail = _fetch(mock_context_factory, html)

    assert detail["title"] == "JSON title"
    assert detail["description"] == "JSON description.\nSecond line."
    assert detail["condition"] == "Used – good"
    assert detail["location"] == "Lugano, TI"
    assert detail["images"] == ["https://scontent.example.net/json_1.jpg", "https://scontent.example.net/json_2.jpg"]
    assert detail["seller_name"] == "Json Seller"
    assert detail["seller_profile_url"] == "https://www.facebook.com/marketplace/profile/900000000000777/"
    assert detail["seller_photo_url"] == "https://scontent.example.net/json_avatar.jpg"
    # Not in the JSON - still read from the page:
    assert detail["posted_at"] == "5 weeks ago"
    assert detail["seller_joined"] == "Joined Facebook in 2023"


def test_fetch_detail_json_condition_found_by_code_not_name_or_position(mock_context_factory):
    """The attribute's name is translated ("Zustand") and it isn't always
    first, but its `value` is one of the itemCondition codes."""
    html = _with_json(
        _ad_page_html(),
        description="A case.",
        attributes=[("Farbe", "gray", "Grau"), ("Zustand", "used_fair", "Gebraucht – akzeptabel")],
    )
    assert _fetch(mock_context_factory, html)["condition"] == "Gebraucht – akzeptabel"


@pytest.mark.parametrize(
    ("geocode", "expected"),
    [
        ({"city": "Lugano", "state": "TI", "postal_code": "6963"}, "Lugano, TI"),
        ({"city": "Germignaga", "state": "", "postal_code": "21010"}, "Germignaga, 21010"),
        ({"city": "Monthey"}, "Monthey"),
    ],
)
def test_fetch_detail_json_location_from_geocode_without_location_text(mock_context_factory, geocode, expected):
    """Rentals have no location_text, only the listing's geocoded location -
    whose city sits in a later copy of `location` than the coordinates, and
    not in the viewer's search area that also carries the listing's id."""
    html = _with_json(rental_detail_html("111"), description="A great rental.", reverse_geocode=geocode)
    assert _fetch(mock_context_factory, html)["location"] == expected


def test_fetch_detail_json_category(mock_context_factory):
    html = _with_json(_ad_page_html(), description="A flat.", category_slug="propertyrentals")
    detail = _fetch(mock_context_factory, html)
    assert detail["category"] == "propertyrentals"
    assert detail["is_rental"] is True


def test_fetch_detail_fills_fields_missing_from_json_from_page(mock_context_factory):
    """JSON with only the photos: everything else comes from the page."""
    html = _with_json(_ad_page_html(), photo_urls=["https://scontent.example.net/json_1.jpg"])
    detail = _fetch(mock_context_factory, html)

    assert detail["images"] == ["https://scontent.example.net/json_1.jpg"]
    assert detail["description"] == "Grey case, looks like new."
    assert detail["condition"] == "New"
    assert detail["location"] == "Volketswil, ZH"
    assert detail["seller_name"] == "Jane Seller"


def test_fetch_detail_ignores_json_for_other_listings(mock_context_factory):
    """Other listings on the page (e.g. Today's picks) have JSON too."""
    html = _ad_page_html().replace(
        "</body>", listing_json_html("222", description="Someone else's item.", location_text="Bern, BE") + "</body>"
    )
    detail = _fetch(mock_context_factory, html)
    assert detail["description"] == "Grey case, looks like new."
    assert detail["location"] == "Volketswil, ZH"


def test_fetch_detail_category_link_with_city_slug(mock_context_factory):
    """Since 2026-10 the category link names the city by slug, not id."""
    html = rental_detail_html("111").replace(
        "/marketplace/109886099040554/propertyrentals/", "/marketplace/zurich/propertyrentals/"
    )
    detail = _fetch(mock_context_factory, html)
    assert detail["category"] == "propertyrentals"
    assert detail["is_rental"] is True


def test_fetch_detail_seller_info_needs_profile_link(mock_context_factory):
    """Logged-out pages have a seller section with no profile link and no
    join date (2026-10). Counting h2s from the end then read a "Related
    searches" entry as the join date - without the link, there's nothing to
    read."""
    detail = _fetch(mock_context_factory, _ad_page_html(seller_name=None))
    assert detail["seller_name"] is None
    assert detail["seller_joined"] is None
    assert detail["seller_photo_url"] is None


# --- Sanity checks -----------------------------------------------------------
#
# See _check_detail() in scraper.py: Facebook changes rarely make the
# scraper fail outright, so these turn wrong-looking results into warnings.

_FULL_JSON = {
    "title": "Pixel 10 Pro case",
    "description": "Grey case, looks like new.",
    "attributes": [("Condition", "new", "New")],
    "location_text": "Volketswil, ZH",
    "seller_id": "900000000000001",
    "seller_name": "Jane Seller",
}


def test_fetch_detail_no_warnings_on_healthy_page(mock_context_factory, caplog):
    with caplog.at_level("DEBUG", logger="fb_scraper.scraper"):
        detail = _fetch(mock_context_factory, _with_json(_ad_page_html(), **_FULL_JSON))
    assert detail["source"] == "json"
    assert detail["warnings"] == []
    assert detail["disagreements"] == []
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_fetch_detail_warns_when_page_has_no_json(mock_context_factory, caplog):
    with caplog.at_level("WARNING", logger="fb_scraper.scraper"):
        detail = _fetch(mock_context_factory, _ad_page_html())
    assert detail["source"] == "page"
    assert detail["warnings"] == ["no page data found - read from the page layout only"]
    assert "listing 111: no page data found" in caplog.text


def test_fetch_detail_warns_when_json_lacks_fields_every_listing_has(mock_context_factory):
    detail = _fetch(mock_context_factory, _with_json(_ad_page_html(), description="Grey case, looks like new."))
    assert detail["source"] == "json"
    assert detail["warnings"] == ["page data has no title, location, seller_name"]


def test_fetch_detail_disagreement_is_debug_not_warning(mock_context_factory, caplog):
    """Expected on vehicle and rental pages, whose layout the page
    extraction misreads - a warning there would fire on every run."""
    html = _with_json(_ad_page_html(), **{**_FULL_JSON, "description": "Something else entirely."})
    with caplog.at_level("DEBUG", logger="fb_scraper.scraper"):
        detail = _fetch(mock_context_factory, html)
    assert detail["disagreements"] == ["description"]
    assert detail["warnings"] == []
    debug = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
    assert "listing 111: page data and page layout differ on description" in debug


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ({"location": "Line one\nline two", "description": "x"}, "location looks wrong: 'Line one\\nline two'"),
        ({"location": "Same text", "description": "Same text"}, "location looks wrong: 'Same text'"),
        ({"location": "x" * 81, "description": "y"}, "location looks wrong: '" + "x" * 60 + "'"),
        ({"description": None, "condition": None, "location": None}, "no description, condition or location found"),
        (
            {"description": "x", "seller_joined": "google pixel 10 pro"},
            "seller_joined has no year: 'google pixel 10 pro'",
        ),
    ],
)
def test_check_detail_flags_values_that_look_wrong(detail, expected):
    from_json = {"found": True, "title": "t", "location": "l", "seller_name": "n"}
    warnings, _ = _check_detail(detail, from_json, {})
    assert warnings == [expected]


@pytest.mark.parametrize(
    "detail",
    [
        {"location": "Germignaga, 21010", "description": "x", "seller_joined": "Joined Facebook in 2009"},
        {"location": "Monthey", "condition": "New"},
        {"location": "Zürich, ZH", "description": "x", "seller_joined": "Seit 2023 auf Facebook"},
    ],
)
def test_check_detail_accepts_normal_values(detail):
    from_json = {"found": True, "title": "t", "location": "l", "seller_name": "n"}
    assert _check_detail(detail, from_json, {}) == ([], [])


def test_visit_all_listings_summarizes_fallbacks_and_warnings(mock_context_factory, caplog):
    detail_map = {"111": _with_json(_ad_page_html(), **_FULL_JSON), "222": _ad_page_html()}
    context = mock_context_factory(detail_html_map=detail_map)
    page = context.new_page()
    listings = [
        {"listing_id": lid, "title": "T", "price": "1 CHF", "location": None, "url": "x", "image_url": None}
        for lid in ("111", "222")
    ]
    with caplog.at_level("INFO", logger="fb_scraper.scraper"):
        visited = visit_all_listings(page, listings, delay=0, verbose=True, fetch_seller_listings=False)
    page.close()

    assert "details: 1/2 listings read from Facebook's page data" in caplog.text
    assert "1 of 2 listings had no page data and were read from the page layout only" in caplog.text
    assert "1 of 2 listings had suspicious values" in caplog.text
    # Bookkeeping only - not part of the scraped results:
    assert not {"source", "warnings", "disagreements"} & set(visited[0])
