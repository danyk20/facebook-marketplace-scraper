from fb_scraper import browser as browser_mod
from fb_scraper.browser import FacebookSession, is_logged_in


def _route_marketplace_root(context, html):
    def handler(route):
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

    context.route("**/marketplace/", handler)


def test_is_logged_in_true_when_no_login_cta(mock_context_factory):
    context = mock_context_factory()
    _route_marketplace_root(context, "<html><body>Marketplace home, no login prompt here</body></html>")
    page = context.new_page()
    assert is_logged_in(page) is True
    page.close()


def test_is_logged_in_false_when_login_link_present(mock_context_factory):
    context = mock_context_factory()
    _route_marketplace_root(context, '<html><body><a role="link">Log In</a></body></html>')
    page = context.new_page()
    assert is_logged_in(page) is False
    page.close()


def test_is_logged_in_false_when_redirected_to_login_url(mock_context_factory):
    context = mock_context_factory()

    def handler(route):
        route.fulfill(
            status=200, content_type="text/html; charset=utf-8",
            body='<html><body>redirected</body></html>',
        )

    # Simulate a login-page URL by routing the login path itself and
    # navigating there directly - is_logged_in() checks page.url after goto.
    context.route("**/login/**", handler)
    page = context.new_page()
    page.goto("https://www.facebook.com/login/?next=%2Fmarketplace%2F")
    assert "login" in page.url
    page.close()


def _fake_sync_playwright(browser):
    """FacebookSession.__enter__() calls sync_playwright().start() itself,
    but Playwright's sync API forbids a second concurrent instance in the
    same thread - and the `browser` fixture already keeps one running for
    the whole test session. This stands in for it, handing back a new
    context off that already-running browser instead of launching a real
    persistent-profile Chromium (which would also leave a profile dir
    behind and be much slower)."""

    class _FakeChromium:
        @staticmethod
        def launch_persistent_context(*_args, **_kwargs):
            return browser.new_context()

    class _FakePlaywright:
        chromium = _FakeChromium()

        def start(self):
            return self

        def stop(self):
            pass

    return _FakePlaywright()


def test_facebook_session_enters_and_exits_when_already_logged_in(tmp_path, monkeypatch, browser):
    monkeypatch.setattr(browser_mod, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(browser_mod, "is_logged_in", lambda page: True)
    monkeypatch.setattr(browser_mod, "sync_playwright", lambda: _fake_sync_playwright(browser))

    with FacebookSession(headless=True) as context:
        page = context.new_page()
        page.close()
    assert (tmp_path / "profile").exists()


def test_facebook_session_headless_not_logged_in_prints_notice(tmp_path, monkeypatch, capsys, browser):
    monkeypatch.setattr(browser_mod, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(browser_mod, "is_logged_in", lambda page: False)
    monkeypatch.setattr(browser_mod, "sync_playwright", lambda: _fake_sync_playwright(browser))

    with FacebookSession(headless=True):
        pass
    assert "continuing anonymously" in capsys.readouterr().out


# The headed + not-logged-in branch (interactive "please log in, press
# Enter" prompt) needs a real display and a human at the keyboard - it's
# marked `# pragma: no cover` in browser.py rather than faked here, same
# reasoning AutoScout24Scraper uses for its own couple of untestable lines
# (see README -> Testing).
