import pytest

from fb_scraper import browser as browser_mod
from fb_scraper.browser import FacebookSession, LoginFailedError, is_logged_in, login_with_credentials


def _route_marketplace_root(context, html):
    def handler(route):
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

    context.route("**/marketplace/", handler)


def test_is_logged_in_true_when_no_login_cta_and_no_email_field(mock_context_factory):
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


def test_is_logged_in_false_when_navbar_login_form_present(mock_context_factory):
    """Confirmed by testing against the real site: Facebook's logged-out
    Marketplace page renders its own login form directly in the top nav
    (real input[name="email"]/input[name="pass"] fields) - this is checked
    because the "Log In" link/text check alone false-positived "logged in"
    on a fresh, never-logged-in profile whose current layout didn't show a
    detectable link."""
    context = mock_context_factory()
    _route_marketplace_root(
        context,
        '<html><body><input name="email"><input name="pass" type="password"></body></html>',
    )
    page = context.new_page()
    assert is_logged_in(page) is False
    page.close()


def test_is_logged_in_false_when_redirected_to_login_url(mock_context_factory):
    # login_wall=True makes any /marketplace/ request 302-redirect to
    # /login/ - the same real-world condition LoginRequiredError detects in
    # scraper.py, exercised here against is_logged_in() itself.
    context = mock_context_factory(login_wall=True)
    page = context.new_page()
    assert is_logged_in(page) is False
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


def _login_form_html(action):
    return f"""
    <html><body>
      <form action="{action}" method="get">
        <input name="email">
        <input name="pass" type="password">
        <input type="submit" value="Login">
      </form>
    </body></html>
    """


def _route_login_form(context, result_path):
    """Serves a login form at /login/ that "submits" (GET) to `result_path`,
    and routes that path to a bare success page - simulating Facebook's real
    login flow without needing real credentials or network access. Test
    credentials used throughout are fake/made up, never real ones."""

    def form_handler(route):
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=_login_form_html(result_path))

    def result_handler(route):
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<html><body>ok</body></html>")

    context.route("**/login/", form_handler)
    context.route(f"**{result_path}*", result_handler)


def test_login_with_credentials_success(mock_context_factory):
    context = mock_context_factory()
    _route_login_form(context, "/after_auth")
    page = context.new_page()
    login_with_credentials(page, "test@example.com", "fake-password-123")
    assert "login" not in page.url
    page.close()


def test_login_with_credentials_raises_on_checkpoint(mock_context_factory):
    context = mock_context_factory()
    _route_login_form(context, "/checkpoint/500")
    page = context.new_page()
    with pytest.raises(LoginFailedError, match="2FA|checkpoint"):
        login_with_credentials(page, "test@example.com", "fake-password-123")
    page.close()


def test_login_with_credentials_raises_on_two_step_verification(mock_context_factory):
    """Confirmed against the real site during development: repeated
    automated logins can make Facebook demand extra verification on a
    correct email/password, landing on /two_step_verification/... rather
    than /checkpoint/ - must not be misreported as a wrong-password failure."""
    context = mock_context_factory()
    _route_login_form(context, "/two_step_verification/authentication/")
    page = context.new_page()
    with pytest.raises(LoginFailedError, match="two-step verification"):
        login_with_credentials(page, "test@example.com", "fake-password-123")
    page.close()


def test_login_with_credentials_raises_when_still_on_login_page(mock_context_factory):
    context = mock_context_factory()
    _route_login_form(context, "/login_failed")
    page = context.new_page()
    with pytest.raises(LoginFailedError, match="did not succeed"):
        login_with_credentials(page, "test@example.com", "wrong-password")
    page.close()


def test_login_with_credentials_raises_when_form_fields_missing_but_still_on_login(mock_context_factory):
    """A login-ish page with no email field and no redirect away from
    /login/ is a genuine failure (unexpected page layout), not "already
    logged in" (see browser.py::login_with_credentials for that fast path -
    not unit-tested here, see the comment there for why)."""
    context = mock_context_factory()

    def handler(route):
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body="<html><body>no form here</body></html>",
        )

    context.route("**/login/", handler)
    page = context.new_page()
    with pytest.raises(LoginFailedError, match="didn't show the expected email field"):
        login_with_credentials(page, "test@example.com", "fake-password-123", timeout_ms=500)
    page.close()


def test_facebook_session_attempts_login_when_not_logged_in(
    tmp_path,
    monkeypatch,
    browser,
    mock_context_factory,
):
    context = mock_context_factory()
    _route_login_form(context, "/after_auth")
    monkeypatch.setattr(browser_mod, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(browser_mod, "sync_playwright", lambda: _fake_sync_playwright_with_context(context))

    calls = {"is_logged_in": 0}

    def _fake_is_logged_in(page):
        calls["is_logged_in"] += 1
        return calls["is_logged_in"] > 1  # not logged in first, logged in after login attempt

    monkeypatch.setattr(browser_mod, "is_logged_in", _fake_is_logged_in)

    login_attempted = {"value": False}
    real_login = browser_mod.login_with_credentials

    def _tracking_login(page, email, password, **kwargs):
        login_attempted["value"] = True
        return real_login(page, email, password, **kwargs)

    monkeypatch.setattr(browser_mod, "login_with_credentials", _tracking_login)

    with FacebookSession(headless=True, email="test@example.com", password="fake-password-123") as ctx:
        assert ctx is context
    assert login_attempted["value"] is True


def test_facebook_session_does_not_attempt_login_when_already_logged_in(
    tmp_path,
    monkeypatch,
    browser,
    mock_context_factory,
):
    """Regression test: unconditionally re-attempting credential login even
    when a session cookie already has us logged in can hit a "remembered
    browser"/account-chooser UI state that isn't the plain email/password
    form, turning a working session into a failed login (confirmed against
    the real site during development) - so this must not be attempted once
    is_logged_in() (now checking for the login form's own fields, not just
    a possibly-absent "Log In" link) already says True."""
    context = mock_context_factory()
    monkeypatch.setattr(browser_mod, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(browser_mod, "is_logged_in", lambda page: True)
    monkeypatch.setattr(browser_mod, "sync_playwright", lambda: _fake_sync_playwright_with_context(context))

    def _boom(*a, **kw):
        raise AssertionError("must not attempt login when already logged in")

    monkeypatch.setattr(browser_mod, "login_with_credentials", _boom)

    with FacebookSession(headless=True, email="test@example.com", password="fake-password-123") as ctx:
        assert ctx is context


def _fake_sync_playwright_with_context(context):
    class _FakeChromium:
        @staticmethod
        def launch_persistent_context(*_args, **_kwargs):
            return context

    class _FakePlaywright:
        chromium = _FakeChromium()

        def start(self):
            return self

        def stop(self):
            pass

    return _FakePlaywright()
