CREATE TABLE notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL CHECK (kind IN ('new', 'price_drop', 'first_run', 'digest', 'alert')),
    topic      TEXT,
    listing_id TEXT,
    payload    TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'dropped')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at    TEXT
);

CREATE INDEX idx_notifications_status ON notifications (status, id);
CREATE INDEX idx_notifications_topic ON notifications (kind, topic, created_at);

CREATE TABLE user_marks (
    listing_id TEXT NOT NULL REFERENCES listings (id),
    mark       TEXT NOT NULL CHECK (mark IN ('fav', 'hidden')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (listing_id, mark)
);

ALTER TABLE runs ADD COLUMN notified_at TEXT;
