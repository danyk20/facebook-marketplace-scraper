"""
Cookie-based Playwright session for Facebook - no API keys, no tokens.

Facebook Marketplace search results are actually readable while logged
out (capped at ~24 results per search, no infinite scroll). Logging in
once removes that cap and enables scrolling for more results. Either way
we drive a real Chromium profile stored in `browser_profile/`, so if you
do log in, the session is reused on every later run.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).resolve().parent.parent / "browser_profile"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def is_logged_in(page) -> bool:
    page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    if "login" in page.url or "checkpoint" in page.url:
        return False
    try:
        login_cta = page.get_by_role("link", name="Log In", exact=False)
        if login_cta.count() > 0 and login_cta.first.is_visible(timeout=1000):
            return False
    except Exception:
        pass
    return True


class FacebookSession:
    """Context manager wrapping a persistent Playwright browser context."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw = None
        self.context = None

    def __enter__(self):
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
        page.close()
        if not logged_in:
            if self.headless:
                print(
                    "Not logged into Facebook - continuing anonymously "
                    "(results capped at ~24 per search). Run with --headed "
                    "once to log in and lift that cap."
                )
            else:  # pragma: no cover - interactive path, needs a real display + a human; see README -> Testing
                print(
                    "\n>>> Please log into Facebook in the opened browser window.\n"
                    ">>> Once you can see Marketplace, come back here and press Enter.\n"
                    ">>> (Or just press Enter to continue without logging in.)\n"
                )
                input()
        return self.context

    def __exit__(self, exc_type, exc, tb):
        if self.context:
            self.context.close()
        if self._pw:
            self._pw.stop()
