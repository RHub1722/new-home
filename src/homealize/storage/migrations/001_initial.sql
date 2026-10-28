CREATE TABLE listings (
    id                TEXT PRIMARY KEY,
    source            TEXT NOT NULL,
    url               TEXT NOT NULL,
    title             TEXT NOT NULL,
    region            TEXT,
    settlement        TEXT,
    zone              TEXT,
    street            TEXT,
    lat               REAL,
    lon               REAL,
    area_sot          REAL,
    land_type         TEXT,
    price_original    REAL,
    currency          TEXT,
    price_eur         REAL,
    price_per_sot_eur REAL,
    author_type       TEXT NOT NULL,
    has_gas           INTEGER,
    has_electricity   INTEGER,
    has_water         INTEGER,
    has_sewerage      INTEGER,
    has_asphalt_road  INTEGER,
    in_settlement     INTEGER,
    description       TEXT,
    images            TEXT NOT NULL DEFAULT '[]',
    site_updated_at   TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    missed_scans      INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL CHECK (status IN ('active', 'removed', 'filtered_out')),
    filter_reason     TEXT
);

CREATE INDEX idx_listings_status ON listings (status);

CREATE TABLE price_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id     TEXT NOT NULL REFERENCES listings (id),
    price_original REAL,
    currency       TEXT,
    price_eur      REAL,
    observed_at    TEXT NOT NULL
);

CREATE INDEX idx_price_history_listing ON price_history (listing_id, observed_at);

CREATE TABLE runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    search_complete INTEGER NOT NULL DEFAULT 0,
    ads_seen        INTEGER NOT NULL DEFAULT 0,
    new             INTEGER NOT NULL DEFAULT 0,
    changed         INTEGER NOT NULL DEFAULT 0,
    price_changed   INTEGER NOT NULL DEFAULT 0,
    removed         INTEGER NOT NULL DEFAULT 0,
    errors          TEXT NOT NULL DEFAULT '[]'
);
