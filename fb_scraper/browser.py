"""
Cookie-based Playwright session for Facebook - no API keys, no tokens.

Facebook Marketplace search used to be readable while logged out; as of
this writing it isn't (see fb_scraper/scraper.py's module docstring and
LoginRequiredError) - logging in is effectively required. Three ways to do
that, in order of convenience:

1. Pass `email`/`password` (to FacebookSession, scrape(), or the CLI's
   --email/--password) - this module fills and submits Facebook's own login
   form for you. Only works if Facebook doesn't challenge the login with a
   2FA/checkpoint step; if it does, LoginFailedError says so explicitly
   rather than hanging or silently failing.
2. Run once with `--headed` (no credentials) and log in by hand in the
   window that opens - handles 2FA fine, since a human is there to answer it.
3. Do nothing - if a previous run already logged in, the session (cookies)
   is reused automatically.

Either way we drive a real Chromium profile stored in a persistent
directory on disk, so a successful login (by any of the three methods) is
reused on every later run without logging in again - see PROFILE_DIR below
for exactly where.
"""

import logging
import os
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

logger = logging.getLogger(__name__)


def _default_profile_dir() -> Path:
    """A stable, install-location-independent default.

    An earlier version of this computed the profile directory from
    `__file__` (two parents up from this module) instead - which happened
    to land in a git checkout's project root when developing this repo
    directly, but for anyone who `pip install`s/`poetry add`s this package
    instead resolves to somewhere inside that virtualenv's `site-packages/`.
    Confirmed by testing (installing into a separate project's own
    Poetry-managed virtualenv): that location is wiped on every reinstall/
    upgrade, and is shared indiscriminately across every unrelated project
    that happens to use the same virtualenv - and, worse, silently forces a
    *fresh* login on first use there even with fully correct credentials,
    which gets Facebook's stricter, more checkpoint-prone treatment of a
    brand-new, unestablished browser session (`LoginFailedError`) instead of
    the smooth "already logged in" path a long-lived profile gets. Anchoring
    on the importing user's home directory instead means the same profile -
    and the same already-trusted login - is found regardless of which
    project or virtualenv imports this package."""
    return Path.home() / ".fb_scraper" / "browser_profile"


# Override with FB_SCRAPER_PROFILE_DIR if you want the profile somewhere
# else entirely (e.g. a shared volume in a container deployment).
PROFILE_DIR = Path(os.environ.get("FB_SCRAPER_PROFILE_DIR") or _default_profile_dir())

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

LOGIN_URL = "https://www.facebook.com/login/"


class LoginFailedError(RuntimeError):
    """Raised when email/password login didn't reach a logged-in state -
    typically because Facebook challenged it with a 2FA/checkpoint step,
    which needs a human and can't be scripted here."""


def dismiss_overlays(page: Page) -> None:
    """Best-effort dismissal of the cookie banner and login nag that
    otherwise sit on top of the page and intercept clicks/scrolls (including
    the login form's own submit button). Both are optional - logged-out
    browsing works without touching either, this just clears the way for
    UI interaction when they do show up."""
    for label in ["Optionale Cookies ablehnen", "Decline optional cookies"]:
        try:
            btn = page.get_by_text(label, exact=False).first
            if btn.is_visible(timeout=800):
                btn.click(timeout=800)
                page.wait_for_timeout(300)
        except Exception:
            pass
    for label in ["Schließen", "Close"]:
        try:
            btn = page.locator(f'div[aria-label="{label}"]').first
            if btn.is_visible(timeout=800):
                btn.click(timeout=800)
                page.wait_for_timeout(300)
        except Exception:
            pass


def is_logged_in(page: Page) -> bool:
    """Confirmed by testing: Facebook's logged-out Marketplace page renders
    its own login form directly in the top nav (real `input[name="email"]`/
    `input[name="pass"]` fields, same names as the dedicated /login/ page) -
    that's a more reliable signal than looking for a "Log In" link/text,
    which Facebook's current logged-out layout doesn't always show in a
    detectable way (confirmed false-positiving "logged in" on a fresh,
    never-logged-in profile without this check)."""
    page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    if "login" in page.url or "checkpoint" in page.url:
        return False
    if page.locator('input[name="email"]').count() > 0:
        return False
    try:
        login_cta = page.get_by_role("link", name="Log In", exact=False)
        if login_cta.count() > 0 and login_cta.first.is_visible(timeout=1000):
            return False
    except Exception:
        pass
    return True


def _click_remembered_account_continue(page: Page, timeout_ms: int) -> bool:
    """Facebook can remember a browser and show an account-chooser screen
    instead of a blank email/password form - a profile picture, the
    account's name, and a "Weiter"/"Continue" button (confirmed by testing;
    our browser is always locale=de-CH, so "Weiter" is what actually shows
    up, "Continue" is a defensive fallback). Matched by visible text, not
    role="button": confirmed by testing that this element is an unstyled
    <span> nested inside a <div role="none"> - Facebook's own UI framework
    doesn't always set a real ARIA role on its custom buttons, the same
    issue login_with_credentials() already works around for the submit
    control by using Enter instead of a button click. Clicks it if present
    and returns whether it did; the caller still needs to handle whatever
    comes next (straight into a logged-in state, or a password-confirmation
    field)."""
    for label in ["Weiter", "Continue"]:
        button = page.get_by_text(label, exact=True)
        if button.count() > 0:
            button.first.click(timeout=timeout_ms)
            return True
    return False


def login_with_credentials(page: Page, email: str, password: str, timeout_ms: int = 15000) -> None:
    """Fill and submit Facebook's own login form. Raises LoginFailedError if
    that doesn't end in a logged-in state (wrong credentials, or a
    2FA/checkpoint challenge Facebook wants a human to answer).

    Safe to call even if the session might already be logged in - three
    cases are handled after navigating to /login/:
      1. A blank email/password form: fill and submit normally.
      2. A "remembered browser" account-chooser screen (profile picture +
         name + a "Weiter"/"Continue" button, no email field) - confirmed
         by testing. Clicks through it, then fills the password if Facebook
         asks to confirm it (it doesn't always).
      3. Redirected past /login/ entirely (no email field, no chooser,
         not a login/checkpoint URL): already logged in, nothing to do.
    This matters because is_logged_in()'s marketplace-page heuristic isn't
    fully reliable (Facebook's logged-out layout doesn't always show a
    detectable "Log In" link), so callers should attempt login whenever they
    have credentials rather than trusting that heuristic to gate it."""
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    dismiss_overlays(page)

    email_field = page.locator('input[name="email"]').first
    if email_field.count() > 0:
        try:
            email_field.fill(email, timeout=timeout_ms)
            password_field = page.locator('input[name="pass"]').first
            password_field.fill(password, timeout=timeout_ms)
            # Submit by pressing Enter rather than clicking a button:
            # Facebook's real type="submit" input is present but wrapped in
            # a hidden div (a styled, locale-specific "Anmelden"/"Log In"
            # element is what's actually visible), so clicking a button
            # selector is both fragile across locales and literally not
            # clickable here. Enter in the password field submits the form
            # the same way clicking would.
            password_field.press("Enter", timeout=timeout_ms)
        except Exception as exc:
            raise LoginFailedError(
                f"Could not fill/submit Facebook's login form (page layout may have changed): {exc}"
            ) from None
    else:
        try:
            clicked_continue = _click_remembered_account_continue(page, timeout_ms)
        except Exception as exc:
            raise LoginFailedError(f"Could not click through the remembered-account screen: {exc}") from None
        if clicked_continue:
            page.wait_for_timeout(2000)
            # Continuing as a remembered account sometimes still asks to
            # confirm the password before actually logging in.
            password_field = page.locator('input[name="pass"]').first
            if password_field.count() > 0:
                try:
                    password_field.fill(password, timeout=timeout_ms)
                    password_field.press("Enter", timeout=timeout_ms)
                except Exception as exc:
                    raise LoginFailedError(f"Could not fill/submit the password-confirmation field: {exc}") from None
        elif "login" not in page.url and "checkpoint" not in page.url:
            # "Already logged in" fast path (Facebook redirected /login/
            # past the form entirely). Not unit-tested: reliably simulating
            # a real cross-navigation HTTP redirect completing inside a
            # mocked BrowserContext proved too fragile/flaky to pin down
            # (Chromium's redirect-chasing interacted unpredictably with
            # route interception in testing) - covered for real by the e2e
            # suite instead, same category of exemption as the two
            # AutoScout24Scraper lines this project's README cites.
            return  # pragma: no cover
        else:
            raise LoginFailedError(
                "Facebook's login form didn't show the expected email field, nor a "
                "recognizable 'continue as' prompt (page layout may have changed)."
            )

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass  # best-effort; the fixed wait below is the real fallback
    page.wait_for_timeout(2000)

    # Confirmed by testing: repeated automated logins from the same
    # environment can make Facebook demand extra verification on a
    # perfectly correct email/password, landing on
    # /two_step_verification/authentication/ - not just /checkpoint/.
    # Checked before the generic "login did not succeed" branch below so
    # this doesn't get misreported as a wrong-password failure.
    if "checkpoint" in page.url or "two_step_verification" in page.url:
        raise LoginFailedError(
            "Facebook is asking for additional verification (2FA/checkpoint/two-step "
            "verification), which needs a human to answer. Run with --headed (no "
            "credentials) and log in by hand instead - the session is then reused on "
            "every later run."
        )
    if "login" in page.url:
        raise LoginFailedError(
            "Login did not succeed - double-check the email/password. If Facebook "
            "is showing a specific error, run with --headed to see it."
        )


class FacebookSession:
    """Context manager wrapping a persistent Playwright browser context."""

    def __init__(self, headless: bool = True, email: str | None = None, password: str | None = None):
        self.headless = headless
        self.email = email
        self.password = password
        self._pw: Playwright | None = None
        self.context: BrowserContext | None = None

    def __enter__(self) -> BrowserContext:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.context = self._pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=self.headless,
            user_agent=USER_AGENT,
            viewport={"width": 1400, "height": 1000},
            locale="de-CH",
            timezone_id="Europe/Zurich",
        )
        page = self.context.new_page()
        logged_in = is_logged_in(page)
        if not logged_in and self.email and self.password:
            # Gated on is_logged_in() (unlike an earlier version of this
            # code): confirmed by testing that attempting login_with_
            # credentials() unconditionally, even when a session cookie
            # already has us logged in, can hit a "remembered browser"/
            # account-chooser UI state that isn't the plain email/password
            # form - login_with_credentials()'s "already logged in" fast
            # path doesn't reliably cover that variant, and re-submitting
            # into it can turn a working session into a failed login. Now
            # that is_logged_in() itself checks for the login form's own
            # input fields (see its docstring) rather than a possibly-absent
            # "Log In" link, it no longer false-positives on a fresh,
            # never-logged-in profile, so gating on it here is safe again.
            login_with_credentials(page, self.email, self.password)
            logged_in = is_logged_in(page)
        page.close()
        if not logged_in:
            if self.headless:
                logger.warning(
                    "Not logged into Facebook - continuing anonymously. Pass "
                    "email/password (--email/--password), or run with --headed "
                    "once to log in by hand; the session is then reused on every "
                    "later run."
                )
            else:  # pragma: no cover - interactive path, needs a real display + a human; see README -> Testing
                print(
                    "\n>>> Please log into Facebook in the opened browser window.\n"
                    ">>> Once you can see Marketplace, come back here and press Enter.\n"
                    ">>> (Or just press Enter to continue without logging in.)\n"
                )
                input()
        return self.context

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.context:
            self.context.close()
        if self._pw:
            self._pw.stop()
