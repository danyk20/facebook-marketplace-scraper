import pytest

from fb_scraper.scraper import LoginRequiredError, _parse_detail_text, fetch_detail, visit_all_listings
from tests.conftest import default_detail_html


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


def test_fetch_detail_raises_login_required_on_redirect(mock_context_factory):
    context = mock_context_factory(login_wall=True)
    page = context.new_page()
    with pytest.raises(LoginRequiredError, match="111"):
        fetch_detail(page, "111")
    page.close()
