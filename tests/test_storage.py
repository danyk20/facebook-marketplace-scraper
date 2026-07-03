from fb_scraper import storage


def test_upsert_listings_marks_new_then_updated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "listings.db")

    item = {"listing_id": "1", "title": "A", "price": "10 CHF", "location": "Zürich, ZH",
            "url": "u", "image_url": "i", "is_local": True}

    result1 = storage.upsert_listings("Tesla", [item])
    assert result1 == {"new": ["1"], "updated": []}

    item["price"] = "20 CHF"
    result2 = storage.upsert_listings("Tesla", [item])
    assert result2 == {"new": [], "updated": ["1"]}


def test_all_listings_filters_by_query_and_locality(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "listings.db")

    local_item = {"listing_id": "1", "title": "A", "price": "10 CHF", "location": "Zürich, ZH",
                  "url": "u", "image_url": "i", "is_local": True}
    foreign_item = {"listing_id": "2", "title": "B", "price": "20 CHF", "location": "Munich, BY",
                     "url": "u2", "image_url": "i2", "is_local": False}
    storage.upsert_listings("Tesla", [local_item, foreign_item])
    storage.upsert_listings("iPhone", [local_item])

    tesla_local = storage.all_listings("Tesla", local_only=True)
    assert [x["listing_id"] for x in tesla_local] == ["1"]

    tesla_all = storage.all_listings("Tesla", local_only=False)
    assert {x["listing_id"] for x in tesla_all} == {"1", "2"}

    iphone_local = storage.all_listings("iPhone")
    assert [x["listing_id"] for x in iphone_local] == ["1"]


def test_upsert_listings_persists_raw_json_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "listings.db")
    item = {"listing_id": "1", "title": "Ünïcödé", "price": "10 CHF", "location": None,
            "url": "u", "image_url": None, "is_local": True}
    storage.upsert_listings("Tesla", [item])
    [stored] = storage.all_listings("Tesla")
    assert stored["title"] == "Ünïcödé"
