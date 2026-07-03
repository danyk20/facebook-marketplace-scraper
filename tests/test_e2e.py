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
"""
import json
import subprocess
import sys

import pytest

from fb_scraper.scraper import scrape

pytestmark = pytest.mark.e2e


def test_real_search_returns_a_valid_scrape_result():
    result = scrape("Tesla Roadster", detail=False, verbose=False, max_scrolls=1)
    assert result.query == "Tesla Roadster"
    assert result.country == "ch"
    assert len(result.rows) == len(result.listings) == result.total_elements
    for row in result.rows:
        assert row["listing_id"]
        assert row["url"].startswith("https://www.facebook.com/marketplace/item/")


def test_real_search_with_detail_and_filters():
    result = scrape(
        "Tesla Roadster", detail=True, verbose=False, max_scrolls=1,
        min_price=0, max_price=10_000_000,
    )
    assert len(result.rows) == len(result.listings) == result.total_elements
    if result.rows:
        assert "condition" in result.rows[0]


def test_real_cli_subprocess_writes_files(tmp_path):
    out_base = str(tmp_path / "roadster")
    proc = subprocess.run(
        [sys.executable, "main.py", "--query", "Tesla Roadster", "--no-detail", "--out", out_base],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    # The JSON file is always written, even for zero results; the CSV is
    # intentionally skipped when there's nothing to write (save_csv() warns
    # instead of writing a header-only file - see test_flatten.py).
    data = json.loads((tmp_path / "roadster.json").read_text())
    assert isinstance(data, list)
    if data:
        assert (tmp_path / "roadster.csv").exists()


def test_real_cli_unknown_country_exits_1():
    proc = subprocess.run(
        [sys.executable, "main.py", "--query", "Tesla", "--country", "xx"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1
    assert "xx" in proc.stderr
