ALTER TABLE listings ADD COLUMN region_id INTEGER;
ALTER TABLE listings ADD COLUMN settlement_id INTEGER;

CREATE INDEX idx_listings_settlement ON listings (settlement_id);

CREATE TABLE poi (
    osm_key    TEXT PRIMARY KEY,
    category   TEXT NOT NULL,
    name       TEXT,
    brand      TEXT,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE route_cache (
    lat_round   REAL NOT NULL,
    lon_round   REAL NOT NULL,
    route_km    REAL,
    route_min   REAL,
    entry       TEXT,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (lat_round, lon_round)
);

CREATE TABLE fx_rates (
    rate_date    TEXT NOT NULL,
    currency     TEXT NOT NULL,
    mdl_per_unit REAL NOT NULL,
    PRIMARY KEY (rate_date, currency)
);

CREATE TABLE scores (
    listing_id                TEXT PRIMARY KEY REFERENCES listings (id),
    computed_at               TEXT NOT NULL,
    price_eur                 REAL,
    price_per_sot_eur         REAL,
    lat                       REAL,
    lon                       REAL,
    coords_approx             INTEGER NOT NULL DEFAULT 0,
    route_km                  REAL,
    route_min                 REAL,
    nearest_entry             TEXT,
    poi_nearest_m             TEXT NOT NULL DEFAULT '{}',
    market_median_eur_per_sot REAL,
    median_level              TEXT,
    median_n                  INTEGER,
    duplicate_group           TEXT,
    is_group_primary          INTEGER NOT NULL DEFAULT 1,
    components                TEXT NOT NULL DEFAULT '{}',
    explanations              TEXT NOT NULL DEFAULT '{}',
    flags                     TEXT NOT NULL DEFAULT '[]',
    total                     REAL NOT NULL,
    excluded_reason           TEXT,
    rank                      INTEGER
);

CREATE INDEX idx_scores_rank ON scores (rank);
