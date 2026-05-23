-- legistar-mcp/src/legistar_mcp/index/schema.sql

CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY,           -- Legistar ID
    file TEXT UNIQUE NOT NULL,        -- e.g., "Int 0153-2022"
    name TEXT,
    title TEXT,
    summary TEXT,
    type_name TEXT,                   -- Introduction / Resolution / ...
    status_name TEXT,                 -- Enacted / Filed / ...
    body_id INTEGER,
    body_name TEXT,
    intro_date TEXT,                  -- ISO date
    enactment_date TEXT,
    last_modified TEXT,
    path TEXT NOT NULL                -- relative path back to source JSON
);
CREATE INDEX IF NOT EXISTS idx_bills_intro_date ON bills(intro_date);
CREATE INDEX IF NOT EXISTS idx_bills_status ON bills(status_name);
CREATE INDEX IF NOT EXISTS idx_bills_body ON bills(body_name);

CREATE VIRTUAL TABLE IF NOT EXISTS bills_fts USING fts5(
    name, title, summary, text,
    content='', tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS bills_fts_map (
    bill_id INTEGER PRIMARY KEY REFERENCES bills(id),
    fts_rowid INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS sponsors (
    bill_id INTEGER NOT NULL REFERENCES bills(id),
    person_slug TEXT NOT NULL,
    sequence INTEGER,
    PRIMARY KEY (bill_id, person_slug)
);
CREATE INDEX IF NOT EXISTS idx_sponsors_person ON sponsors(person_slug);

CREATE TABLE IF NOT EXISTS people (
    slug TEXT PRIMARY KEY,
    id INTEGER UNIQUE,
    full_name TEXT,
    is_active INTEGER,
    start_date TEXT,
    end_date TEXT,
    path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_people_active ON people(is_active);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    body_id INTEGER,
    body_name TEXT,
    date TEXT,                        -- ISO datetime
    location TEXT,
    last_modified TEXT,
    path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE INDEX IF NOT EXISTS idx_events_body ON events(body_name);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    item_title, agenda_note, minutes_note,
    content='', tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS events_fts_map (
    event_id INTEGER NOT NULL REFERENCES events(id),
    fts_rowid INTEGER NOT NULL UNIQUE,
    item_sequence INTEGER,
    PRIMARY KEY (event_id, item_sequence)
);

CREATE TABLE IF NOT EXISTS index_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
