#!/usr/bin/env python3
"""
Thin wrapper for local `python main.py` usage. The real CLI lives in
fb_scraper/cli.py (also exposed as the `facebook-marketplace-scraper`
console script once this package is pip-installed) - this file exists so
the project keeps working exactly as before for anyone running it straight
from a git clone without installing it.
"""

from __future__ import annotations

import sys

from fb_scraper.cli import run_cli

if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests/test_e2e.py
    sys.exit(run_cli())
