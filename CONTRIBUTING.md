# Contributing

Thanks for considering a contribution — human or AI agent, both welcome (see
the [License](README.md#license) section of the README).

## Dev setup

```bash
git clone https://github.com/danyk20/facebook-marketplace-scraper.git
cd facebook-marketplace-scraper
pipenv install --dev
pipenv run python -m playwright install chromium
```

## Before opening a PR

```bash
pipenv run ruff check .          # lint
pipenv run ruff format .         # format
pipenv run mypy                  # type-check
pipenv run pytest                # unit tests, must stay at or above 90% coverage
```

If your change touches how listings are found or parsed (search URL
parameters, tile/detail-page parsing, login flow), also run the end-to-end
suite against the real site (needs a logged-in test account — see
`tests/test_e2e.py`'s module docstring for the `FB_TEST_EMAIL`/
`FB_TEST_PASSWORD` environment variables it reads):

```bash
FB_TEST_EMAIL=... FB_TEST_PASSWORD=... pipenv run pytest -m e2e --no-cov
```

## Expectations

- **Every behavior change needs a test.** The unit suite mocks all browser
  network traffic (via Playwright's `BrowserContext.route()`, see
  `tests/conftest.py`) and enforces a 90% coverage floor — a change without
  a test risks failing CI on that basis alone.
- **Keep `verbose`/logging output backward compatible** unless the PR is
  specifically about changing it — other code (and the e2e/CLI tests)
  depends on the current message wording.
- If Facebook changes Marketplace's markup or URL parameters, prefer fixing
  the affected function directly over adding a workaround —
  `fb_scraper/scraper.py`'s module docstring documents how the current
  request shape and parsing were reverse-engineered.
- Keep the change minimal and focused. If you're changing something that
  also exists in the sibling project,
  [AutoScout24Scraper](https://github.com/danyk20/autoscout24-scraper),
  consider whether the same fix/idea applies there too (see
  [Relationship to AutoScout24Scraper](README.md#relationship-to-autoscout24scraper)).
- Never commit real Facebook credentials (or any other secret) anywhere in
  the repo, including test fixtures/commit messages — this repo is public.

## Questions / bug reports

Open a GitHub issue using the bug report template — include the exact
command you ran, whether you were logged in, and any error message you got
back. Facebook changing its markup/API shape without notice is this
project's main long-term risk, so a precise repro matters a lot here.

## Releasing (maintainer only)

Publishing to PyPI is automated via `.github/workflows/release.yml` using
PyPI Trusted Publishing (no API tokens stored anywhere) — pushing a tag is
the only manual step:

1. Bump `__version__` in `fb_scraper/__init__.py`.
2. Add a new entry at the top of `CHANGELOG.md` (Keep a Changelog format).
3. Commit those two changes, then tag and push:
   ```bash
   git commit -am "Release vX.Y.Z"
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```
4. The release workflow verifies `__version__` matches the tag (fails fast
   if they disagree), builds, publishes to TestPyPI, then to real PyPI.
   Watch the Actions tab.
5. To dry-run the pipeline without a real release, push a pre-release tag
   instead (e.g. `vX.Y.Z-rc1`) — it publishes to TestPyPI only and never
   reaches real PyPI, since the version/tag check and the real-PyPI job
   both key off an exact `vX.Y.Z` tag.

One-time setup this depends on (already documented, done once): a Trusted
Publisher registered on both pypi.org and test.pypi.org for this repo, and
matching GitHub Environments named `pypi`/`testpypi`.
