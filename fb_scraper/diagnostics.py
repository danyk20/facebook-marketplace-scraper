"""
Saving what the browser showed when something went wrong.

Most failures this scraper can hit are intermittent - a Facebook dialog
that didn't open once, then opens fine on every retry - so by the time
anyone looks, the page that caused it is gone. Each failure therefore gets
its own folder under DEBUG_DIR (~/.fb_scraper/debug by default, next to the
browser profile; override with FB_SCRAPER_DEBUG_DIR) holding:

    page.png    a screenshot of the browser page
    page.html   the page's HTML
    page.txt    its URL, title and the dialogs that were open
    run.log     the run's full log (written by the CLI - see cli.run_cli())

Only the newest KEEP_DEBUG_DIRS folders are kept. Everything here is
best-effort: failing to save a snapshot must never hide the original error.
These files can hold personal data from the page (the account's name, the
listings and messages it shows), so they stay on this machine.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page

logger = logging.getLogger(__name__)

DEBUG_DIR = Path(os.environ.get("FB_SCRAPER_DEBUG_DIR") or Path.home() / ".fb_scraper" / "debug")
KEEP_DEBUG_DIRS = 20

# What visible_dialogs() reports per dialog: its aria-label if it has one,
# else the start of its text.
_VISIBLE_DIALOGS_JS = """
() => Array.from(document.querySelectorAll('[role="dialog"]'))
    .filter((d) => d.offsetWidth || d.offsetHeight)
    .map((d) => d.getAttribute('aria-label')
        || (d.innerText || '').split('\\n').map((line) => line.trim()).filter(Boolean).slice(0, 3).join(' / '))
    .map((s) => s.slice(0, 100))
"""

# Exceptions carry their debug folder in this attribute, so the CLI can add
# the run's log to the same folder - see attach_debug_dir().
_ATTR = "fb_scraper_debug_dir"


def new_debug_dir(label: str) -> Path:
    """A new, empty folder for one failure, named after the time and
    `label` - e.g. 2026-11-04_15-30-12_lookup-city. Removes the oldest
    folders beyond KEEP_DEBUG_DIRS."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "error"
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = DEBUG_DIR / f"{stamp}_{slug}"
    suffix = 1
    while path.exists():
        suffix += 1
        path = DEBUG_DIR / f"{stamp}_{slug}-{suffix}"
    path.mkdir()
    old = sorted(p for p in DEBUG_DIR.iterdir() if p.is_dir())[:-KEEP_DEBUG_DIRS]
    for p in old:
        shutil.rmtree(p, ignore_errors=True)
    return path


def visible_dialogs(page: Page) -> list[str]:
    """A short description of each dialog currently shown on the page."""
    try:
        return list(page.evaluate(_VISIBLE_DIALOGS_JS))
    except Exception:  # page closed or navigating - nothing to describe
        return []


def save_page_snapshot(page: Page, label: str) -> Path | None:
    """Save page.png, page.html and page.txt for `page` into a new debug
    folder and return it - or None if not even the folder could be made."""
    try:
        directory = new_debug_dir(label)
    except OSError:
        logger.debug("couldn't create a debug folder under %s", DEBUG_DIR, exc_info=True)
        return None
    try:
        page.screenshot(path=str(directory / "page.png"), timeout=5000)
    except Exception:
        logger.debug("couldn't save a screenshot", exc_info=True)
    try:
        (directory / "page.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        logger.debug("couldn't save the page's HTML", exc_info=True)
    try:
        title = page.title()
    except Exception:
        title = "?"
    dialogs = visible_dialogs(page)
    summary = [f"url: {page.url}", f"title: {title}", "open dialogs:"] + [f"  - {d}" for d in dialogs or ["(none)"]]
    try:
        (directory / "page.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    except OSError:
        logger.debug("couldn't save page.txt", exc_info=True)
    return directory


def attach_debug_dir(exc: BaseException, directory: Path | None) -> None:
    """Remember `directory` on `exc` (see debug_dir_of())."""
    if directory is not None:
        try:
            setattr(exc, _ATTR, directory)
        except AttributeError:  # an exception type that doesn't allow new attributes
            pass


def debug_dir_of(exc: BaseException) -> Path | None:
    """The debug folder saved for `exc`, if any."""
    return getattr(exc, _ATTR, None)
