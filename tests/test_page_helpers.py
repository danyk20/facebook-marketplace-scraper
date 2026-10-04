from fb_scraper import scraper
from fb_scraper.browser import dismiss_overlays
from fb_scraper.scraper import scroll_to_load


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
    dismiss_overlays(page)  # must not raise, even though both elements exist and are clickable
    assert page.locator("#marker").is_visible()
    page.close()


def test_dismiss_overlays_is_a_no_op_when_nothing_to_dismiss(mock_context_factory):
    context = mock_context_factory(search_html="<html><body><p id='marker'>hi</p></body></html>")
    page = context.new_page()
    page.goto("https://www.facebook.com/marketplace/zurich/search?query=x")
    dismiss_overlays(page)  # must not raise
    assert page.locator("#marker").is_visible()
    page.close()


def test_scroll_to_load_stops_when_height_stops_changing(mock_context_factory):
    context = mock_context_factory(search_html="<html><body style='height:100px'>static</body></html>")
    page = context.new_page()
    page.goto("https://www.facebook.com/marketplace/zurich/search?query=x")
    scroll_to_load(page, max_scrolls=8, pause_ms=10)  # should return quickly, not loop 8 times
    page.close()


class _FakeScrollPage:
    """Just enough of a Page for scroll_to_load(): records each scroll and
    pause, and reports the next page height from `heights` (the last one
    repeats once they run out)."""

    def __init__(self, heights):
        self.heights = list(heights)
        self.wheels = []
        self.waits = []
        self.mouse = self

    def wheel(self, dx, dy):
        self.wheels.append(dy)

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def evaluate(self, _js):
        return self.heights.pop(0) if len(self.heights) > 1 else self.heights[0]


def test_scroll_to_load_stops_when_facebook_says_results_ended():
    page = _FakeScrollPage(range(100, 10_000, 100))  # page keeps growing
    scroll_to_load(page, max_scrolls=50, pause_ms=10, is_done=lambda: len(page.wheels) >= 3)
    assert len(page.wheels) == 3


def test_scroll_to_load_waits_out_quiet_scrolls_before_giving_up():
    """Facebook is sometimes slower than one pause, and even sends empty
    batches mid-way through results - two quiet scrolls followed by more
    results must not end the scrolling."""
    page = _FakeScrollPage([100, 200, 200, 200, 300])
    scroll_to_load(page, max_scrolls=50, pause_ms=10, idle_limit=5)
    # 300 is reached on scroll 5, then 5 quiet scrolls in a row end it
    assert len(page.wheels) == 10


def test_scroll_to_load_new_responses_count_as_progress():
    page = _FakeScrollPage([100])  # height never changes...
    responses = iter(range(1, 4))  # ...but 3 more batches arrive, then nothing
    last = 0

    def progress():
        nonlocal last
        last = next(responses, last)
        return last

    scroll_to_load(page, max_scrolls=50, pause_ms=10, progress=progress, idle_limit=5)
    assert len(page.wheels) == 3 + 5


def test_scroll_to_load_respects_max_scrolls():
    page = _FakeScrollPage(range(100, 10_000, 100))
    scroll_to_load(page, max_scrolls=7, pause_ms=10)
    assert len(page.wheels) == 7


def test_scroll_to_load_randomizes_distance_and_pause():
    page = _FakeScrollPage(range(100, 10_000, 100))
    scroll_to_load(page, max_scrolls=40, pause_ms=1000)
    assert all(3000 <= dy <= 5000 for dy in page.wheels)
    assert all(800 <= ms <= 1600 for ms in page.waits)
    assert len(set(page.waits)) > 1, "pauses should vary, not repeat one fixed value"


def test_scroll_to_load_default_pause_comes_from_module_setting(monkeypatch):
    monkeypatch.setattr(scraper, "SCROLL_PAUSE_MS", 100)
    page = _FakeScrollPage([100])
    scroll_to_load(page, max_scrolls=3)
    assert all(80 <= ms <= 160 for ms in page.waits)
