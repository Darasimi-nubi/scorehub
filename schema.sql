CREATE TABLE IF NOT EXISTS leagues (
    id      INTEGER PRIMARY KEY,
    sport   TEXT NOT NULL,                 -- football, basketball, hockey
    name    TEXT NOT NULL,
    country TEXT,
    UNIQUE (sport, name)
);

CREATE TABLE IF NOT EXISTS teams (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    crest TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    id           INTEGER PRIMARY KEY,
    external_id  TEXT UNIQUE,              -- id from the data provider
    slug         TEXT,                     -- readable link part, e.g. arsenal-v-chelsea-2026-10-16
    league_id    INTEGER NOT NULL REFERENCES leagues(id),
    home_id      INTEGER NOT NULL REFERENCES teams(id),
    away_id      INTEGER NOT NULL REFERENCES teams(id),
    kickoff      TEXT NOT NULL,            -- UTC, ISO 8601
    status       TEXT NOT NULL DEFAULT 'scheduled'
                 CHECK (status IN ('scheduled', 'live', 'finished', 'postponed')),
    home_score   INTEGER,
    away_score   INTEGER,
    period       TEXT,                     -- football: '1H','HT','2H'; basketball: 'Q1'..'Q4','OT'
    clock        TEXT,                     -- football: minute like "63'"; basketball: "05:12"
    updated_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches (kickoff);
CREATE INDEX IF NOT EXISTS idx_matches_status  ON matches (status);