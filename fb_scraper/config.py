"""
Country configuration for the Facebook Marketplace scraper.

Facebook Marketplace has no "whole country" search - every search is
anchored on a city, with a radius (`radius` URL param, kilometers).
COUNTRY_ANCHORS maps a country code to the anchor city slug to search from
and a radius wide enough to cover the whole country from that one point.

Only "ch" is implemented/confirmed as of this writing - deliberately kept
as a parameter (rather than hardcoding Switzerland) so this scrapes another
country with zero code changes once its anchor is known, the same way
AutoScout24Scraper's `domain` parameter is designed. See README ->
"Countries" for how to find and add another one.
"""

from __future__ import annotations

from typing import TypedDict


class CountryAnchor(TypedDict):
    slug: str
    radius_km: int


class RegionHints(TypedDict):
    names: list[str]
    subdivisions: set[str]


DEFAULT_COUNTRY = "ch"

COUNTRY_ANCHORS: dict[str, CountryAnchor] = {
    # Zurich, 500 km radius (Facebook's max). Verified: for a real query
    # ("Tesla Model S") this returned the *same* 24 listings at Facebook's
    # default 65 km radius as at the 500 km max - i.e. one anchor near the
    # geographic/population center already gives national coverage, since
    # Switzerland's longest axis is ~350 km.
    "ch": {"slug": "zurich", "radius_km": 500},
}

# Region hints used to decide whether a listing's location is actually
# inside the requested country - Facebook's radius search can spill just
# over a border, and this filters those out. Keyed by country code so a
# new country can add its own canton/state abbreviations and country-name
# spellings without touching any scraper code.
COUNTRY_REGION_HINTS: dict[str, RegionHints] = {
    "ch": {
        "names": ["switzerland", "schweiz", "suisse", "svizzera"],
        "subdivisions": {
            "AG",
            "AI",
            "AR",
            "BE",
            "BL",
            "BS",
            "FR",
            "GE",
            "GL",
            "GR",
            "JU",
            "LU",
            "NE",
            "NW",
            "OW",
            "SG",
            "SH",
            "SO",
            "SZ",
            "TG",
            "TI",
            "UR",
            "VD",
            "VS",
            "ZG",
            "ZH",
        },
    },
}


def anchor_for(country: str) -> CountryAnchor:
    try:
        return COUNTRY_ANCHORS[country]
    except KeyError:
        available = ", ".join(sorted(COUNTRY_ANCHORS))
        raise ValueError(
            f"No anchor location configured for country {country!r}. "
            f"Available: {available}. See README -> Countries for how to add one."
        ) from None


def is_local(location: str | None, country: str) -> bool:
    """Does a "City, XX" / "City, Country" location string look like it's
    actually inside `country`? Falls back to True (don't filter) if we have
    no region hints configured for that country."""
    hints = COUNTRY_REGION_HINTS.get(country)
    if not hints or not location:
        return True
    region = location.split(",")[-1].strip()
    if region.upper() in hints["subdivisions"]:
        return True
    return any(name in location.lower() for name in hints["names"])
