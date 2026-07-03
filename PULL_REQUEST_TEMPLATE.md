## What does this change and why?

## Checklist

- [ ] `pipenv run ruff check .` and `pipenv run ruff format --check .` pass
- [ ] `pipenv run mypy` passes
- [ ] `pipenv run pytest` passes with coverage still at or above 90%
- [ ] Added/updated tests for any behavior change
- [ ] If this touches search/parsing/login behavior, ran the e2e suite against the real site
- [ ] Updated the README if this changes CLI flags, the `scrape()`/`ScrapeResult` API, or the documented data shape
