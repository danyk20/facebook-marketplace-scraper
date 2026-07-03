import csv
import json

from fb_scraper.scraper import (
    _price_number,
    flatten_listing,
    order_fieldnames,
    save_csv,
    save_json,
)


def test_flatten_listing_joins_images_list():
    item = {"listing_id": "1", "title": "X", "images": ["a.jpg", "b.jpg"]}
    flat = flatten_listing(item)
    assert flat["images"] == "a.jpg; b.jpg"


def test_flatten_listing_leaves_non_list_fields_untouched():
    item = {"listing_id": "1", "price": "10 CHF", "images": None}
    flat = flatten_listing(item)
    assert flat["price"] == "10 CHF"
    assert flat["images"] is None


def test_order_fieldnames_priority_first_then_alphabetical():
    keys = {"zzz_extra", "aaa_extra", "listing_id", "price", "title"}
    ordered = order_fieldnames(keys)
    assert ordered[:3] == ["listing_id", "title", "price"]
    assert ordered[3:] == ["aaa_extra", "zzz_extra"]


def test_price_number_parses_swiss_thousand_separator():
    assert _price_number("16.900\xa0CHF") == 16900
    assert _price_number("50 CHF") == 50
    assert _price_number(None) is None
    assert _price_number("Free / Kostenlos") is None


def test_save_csv_handles_heterogeneous_rows(tmp_path):
    rows = [
        {"listing_id": "1", "title": "A", "extra_a": "x"},
        {"listing_id": "2", "title": "B", "extra_b": "y"},
    ]
    path = tmp_path / "out.csv"
    save_csv(rows, str(path))

    with open(path, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    assert reader[0]["extra_b"] == ""
    assert reader[1]["extra_a"] == ""


def test_save_csv_no_rows_warns_and_does_not_crash(tmp_path, caplog):
    path = tmp_path / "out.csv"
    with caplog.at_level("WARNING", logger="fb_scraper.scraper"):
        save_csv([], str(path))
    assert not path.exists()
    assert "no rows" in caplog.text


def test_save_json_round_trips_unicode(tmp_path):
    rows = [{"title": "Zürich Öffnungszeiten"}]
    path = tmp_path / "out.json"
    save_json(rows, str(path))
    assert json.loads(path.read_text(encoding="utf-8")) == rows
