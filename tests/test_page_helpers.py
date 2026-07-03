from fb_scraper.scraper import _dismiss_overlays, scroll_to_load


def test_dismiss_overlays_clicks_cookie_banner_and_close_button(mock_context_factory):
    html = """
    <html><body>
      <button>Optionale Cookies ablehnen</button>
      <div aria-label="Schließen" role="button">X</div>
      <p id="marker">still here</p>
    </body></html>
    """
    context = mock_context_factory(search_html=html)
    page = context.new_page()
    page.goto("https://www.facebook.com/marketplace/zurich/search?query=x")
    _dismiss_overlays(page)  # must not raise, even though both elements exist and are clickable
    assert page.locator("#marker").is_visible()
    page.close()


def test_dismiss_overlays_is_a_no_op_when_nothing_to_dismiss(mock_context_factory):
    context = mock_context_factory(search_html="<html><body><p id='marker'>hi</p></body></html>")
    page = context.new_page()
    page.goto("https://www.facebook.com/marketplace/zurich/search?query=x")
    _dismiss_overlays(page)  # must not raise
    assert page.locator("#marker").is_visible()
    page.close()


def test_scroll_to_load_stops_when_height_stops_changing(mock_context_factory):
    context = mock_context_factory(search_html="<html><body style='height:100px'>static</body></html>")
    page = context.new_page()
    page.goto("https://www.facebook.com/marketplace/zurich/search?query=x")
    scroll_to_load(page, max_scrolls=8, pause_ms=10)  # should return quickly, not loop 8 times
    page.close()
