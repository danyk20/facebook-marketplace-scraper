import pytest

from fb_scraper import config


def test_anchor_for_known_country():
    anchor = config.anchor_for("ch")
    assert anchor["slug"] == "zurich"
    assert anchor["radius_km"] == 500


def test_anchor_for_unknown_country_lists_available():
    with pytest.raises(ValueError, match="ch"):
        config.anchor_for("de")


def test_is_local_matches_canton_abbreviation():
    assert config.is_local("Zürich, ZH", "ch") is True
    assert config.is_local("Genève, GE", "ch") is True


def test_is_local_matches_country_name():
    assert config.is_local("Basel, Switzerland", "ch") is True


def test_is_local_rejects_foreign_region():
    assert config.is_local("Munich, BY", "ch") is False


def test_is_local_defensive_on_missing_location():
    assert config.is_local(None, "ch") is True
    assert config.is_local("", "ch") is True


def test_is_local_no_hints_configured_defaults_to_true():
    assert config.is_local("Anywhere, XX", "unconfigured-country") is True
