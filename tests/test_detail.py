import pytest

from fb_scraper.scraper import LoginRequiredError, _parse_detail_text, fetch_detail, visit_all_listings
from tests.conftest import default_detail_html, rental_detail_html, structural_detail_html

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
