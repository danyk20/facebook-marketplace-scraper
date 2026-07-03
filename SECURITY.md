# Security Policy

This project drives a real browser (Playwright/Chromium) against
facebook.com and writes local CSV/JSON files. Unlike a pure-HTTP scraper,
it optionally handles real credentials (`--email`/`--password`, or
`FB_EMAIL`/`FB_PASSWORD`) to log into Facebook — those are passed straight
into Playwright's own form-filling and are never logged, written to disk,
or sent anywhere other than facebook.com's own login form. The persistent
`browser_profile/` directory holds session cookies (not the password
itself) and is gitignored; treat it like any other credential store on
disk. Its other attack surface is small but real (how URLs/paths are
constructed from input, how dependencies are pinned).

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue:

- Preferred: use [GitHub's private vulnerability reporting](https://github.com/danyk20/facebook-marketplace-scraper/security/advisories/new)
  for this repository.
- Alternatively, email vulnerability@danielkosc.eu with a description and,
  if possible, steps to reproduce.

Please allow a reasonable amount of time to respond and address the issue
before any public disclosure. This is a small, single-maintainer project —
response time is best-effort, not guaranteed on an SLA.

## Supported versions

Only the latest commit on `main` is supported. There are no long-term
maintenance branches.
