"""
Optional SQLite-backed tracking of scraped listings across repeated runs,
keyed by search query + listing id. Not part of the core scrape()/CLI flow
(which, like AutoScout24Scraper, just writes a CSV + JSON snapshot per run)
- import and call this yourself if you want to know which listings are new
since the last time you ran the same search.

    from fb_scraper.scraper import scrape
    from fb_scraper.storage import upsert_listings

    result = scrape("Tesla Model S")
    diff = upsert_listings("Tesla Model S", result.listings)
    print(f"{len(diff['new'])} new listings since last run")
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "listings.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    query TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    title TEXT,
    price TEXT,
    location TEXT,
    url TEXT,
    image_url TEXT,
    is_local INTEGER,
    first_seen TEXT,
    last_seen TEXT,
    raw_json TEXT,
    PRIMARY KEY (query, listing_id)
);
"""


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    return conn


def upsert_listings(query, listings):
    """Insert/update listings for this search query, return {'new': [...], 'updated': [...]} listing ids."""
    now = datetime.now(timezone.utc).isoformat()
    new_ids, updated_ids = [], []
    conn = _connect()
    try:
        cur = conn.cursor()
        for item in listings:
            cur.execute(
                "SELECT listing_id FROM listings WHERE query=? AND listing_id=?",
                (query, item["listing_id"]),
            )
            exists = cur.fetchone() is not None
            cur.execute(
                """
                INSERT INTO listings
                    (query, listing_id, title, price, location, url, image_url,
                     is_local, first_seen, last_seen, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(query, listing_id) DO UPDATE SET
                    title=excluded.title,
                    price=excluded.price,
                    location=excluded.location,
                    url=excluded.url,
                    image_url=excluded.image_url,
                    is_local=excluded.is_local,
                    last_seen=excluded.last_seen,
                    raw_json=excluded.raw_json
                """,
                (
                    query,
                    item["listing_id"],
                    item.get("title"),
                    item.get("price"),
                    item.get("location"),
                    item.get("url"),
                    item.get("image_url"),
                    1 if item.get("is_local") else 0,
                    now,
                    now,
                    json.dumps(item, ensure_ascii=False),
                ),
            )
            (updated_ids if exists else new_ids).append(item["listing_id"])
        conn.commit()
    finally:
        conn.close()
    return {"new": new_ids, "updated": updated_ids}


def all_listings(query, local_only=True):
    conn = _connect()
    try:
        cur = conn.cursor()
        sql = "SELECT raw_json FROM listings WHERE query=?"
        params = [query]
        if local_only:
            sql += " AND is_local=1"
        cur.execute(sql, params)
        return [json.loads(row[0]) for row in cur.fetchall()]
    finally:
        conn.close()
