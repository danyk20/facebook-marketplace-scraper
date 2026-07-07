import pytest

from fb_scraper.scraper import LoginRequiredError, _parse_detail_text, fetch_detail, visit_all_listings
from tests.conftest import default_detail_html, rental_detail_html


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


def test_fetch_detail_raises_login_required_on_redirect(mock_context_factory):
    context = mock_context_factory(login_wall=True)
    page = context.new_page()
    with pytest.raises(LoginRequiredError, match="111"):
        fetch_detail(page, "111")
    page.close()
