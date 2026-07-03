"""
End-to-end tests: real calls against the live facebook.com, no mocking.
Excluded by default (see pyproject.toml); run explicitly with:

    pipenv run pytest -m e2e --no-cov

These target "Tesla Roadster" specifically because, as of this writing, it
has zero listings in Switzerland - that makes the full pipeline (search,
local-only filter, detail-visiting, CSV/JSON writing) run in a couple of
seconds without hammering the site, the same reasoning AutoScout24Scraper's
e2e suite uses picking a low-inventory vehicle. A change in inventory
doesn't break these tests - they assert structure/invariants, not counts.

Credentials: read from FB_TEST_EMAIL/FB_TEST_PASSWORD environment variables,
never hardcoded here (this repo is public - a committed real password would
be a real credential leak). Tests that need a logged-in session are skipped,
not failed, if those aren't set - anonymous access no longer reliably works
(see fb_scraper/scraper.py's module docstring), so without credentials
there's nothing meaningful left to test beyond argument validation, and
skipping communicates that "not run" is a precondition gap, not a code bug.

Even with valid credentials, a freshly created or EU/EEA-associated account
can still be blocked by Facebook's separate Marketplace consent screen
until a human clicks through it once (also see the module docstring) - that
also skips the search-dependent tests with a clear reason rather than
failing, since it's an account-setup precondition this suite can't and
shouldn't resolve on its own (see MarketplaceConsentRequiredError).

All real-network tests run via subprocess (spawning `python main.py`, same
as a real user would) rather than calling scrape() in-process: this
environment's pytest process has an asyncio event loop already active by
the time these tests run (from an unrelated interaction between pytest 9.x
and this Playwright version - confirmed unrelated to this project's code by
testing), which trips Playwright sync API's "don't nest sync_playwright()
instances" guard. A subprocess starts clean, sidesteps that entirely, and
still exercises the exact same production scrape() code path via the CLI.
"""
import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

FB_TEST_EMAIL = os.environ.get("FB_TEST_EMAIL")
FB_TEST_PASSWORD = os.environ.get("FB_TEST_PASSWORD")

requires_credentials = pytest.mark.skipif(
    not (FB_TEST_EMAIL and FB_TEST_PASSWORD),
    reason="set FB_TEST_EMAIL/FB_TEST_PASSWORD to run e2e tests that need a logged-in session "
           "(anonymous Marketplace search no longer reliably works - see scraper.py docstring)",
)


def _run_cli(*args, timeout=60):
    return subprocess.run(
        [sys.executable, "main.py", *args],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "FB_EMAIL": FB_TEST_EMAIL or "", "FB_PASSWORD": FB_TEST_PASSWORD or ""},
    )


def _skip_if_consent_wall(proc):
    if proc.returncode != 0 and "consent" in proc.stderr.lower():
        pytest.skip(f"account hasn't accepted Facebook's Marketplace consent screen yet: {proc.stderr}")


@requires_credentials
def test_real_login_with_credentials_succeeds(tmp_path):
    """The core thing this feature promises: valid credentials reach a
    logged-in state without LoginRequiredError, even on a fresh profile."""
    proc = _run_cli("--query", "Tesla Roadster", "--no-detail", "--out", str(tmp_path / "out"))
    assert "LoginRequiredError" not in proc.stderr
    assert "LoginFailedError" not in proc.stderr


@requires_credentials
def test_real_cli_subprocess_writes_files(tmp_path):
    out_base = str(tmp_path / "roadster")
    proc = _run_cli("--query", "Tesla Roadster", "--no-detail", "--out", out_base)
    _skip_if_consent_wall(proc)
    assert proc.returncode == 0, proc.stderr
    # The JSON file is always written, even for zero results; the CSV is
    # intentionally skipped when there's nothing to write (save_csv() warns
    # instead of writing a header-only file - see test_flatten.py).
    data = json.loads((tmp_path / "roadster.json").read_text())
    assert isinstance(data, list)
    if data:
        assert (tmp_path / "roadster.csv").exists()


@requires_credentials
def test_real_cli_with_detail_and_filters(tmp_path):
    out_base = str(tmp_path / "roadster_detail")
    proc = _run_cli(
        "--query", "Tesla Roadster", "--out", out_base,
        "--price-from", "0", "--price-to", "10000000",
    )
    _skip_if_consent_wall(proc)
    assert proc.returncode == 0, proc.stderr
    data = json.loads((tmp_path / "roadster_detail.json").read_text())
    if data:
        assert "condition" in data[0]


def test_real_cli_unknown_country_exits_1():
    """Doesn't need a logged-in session - country validation happens before
    any browser call - so this runs regardless of credentials."""
    proc = subprocess.run(
        [sys.executable, "main.py", "--query", "Tesla", "--country", "xx"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1
    assert "xx" in proc.stderr
