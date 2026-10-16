"""Permanent match ids and readable match links.

The database on Railway is rebuilt after every deploy, so ids handed out by SQLite would change and
old links (and starred matches) would point at different games. Instead:

  * a match's id is computed from the data provider's own match id, so it is the same every time;
  * each match also gets a readable slug, like "fluminense-v-palmeiras-2026-10-14", used in its link.
"""
import hashlib
import re
import unicodedata


def stable_id(external_id):
    """A positive number below 2**31 that depends only on the provider's match id."""
    return int(hashlib.sha1(external_id.encode("utf-8")).hexdigest()[:8], 16) % (2 ** 31 - 1) + 1


def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "team"


def base_slug(home, away, kickoff):
    return f"{slugify(home)}-v-{slugify(away)}-{(kickoff or '')[:10]}"


def ensure_schema(conn):
    """Adds the slug column (and its index) to databases created before it existed, and fills it in."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(matches)")]
    if "slug" not in cols:
        conn.execute("ALTER TABLE matches ADD COLUMN slug TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_slug ON matches (slug)")
    rows = conn.execute("""SELECT m.id, m.external_id, m.kickoff, h.name, a.name FROM matches m
                           JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id
                           WHERE m.slug IS NULL""").fetchall()
    for mid, ext, kickoff, home, away in rows:
        conn.execute("UPDATE matches SET slug = ? WHERE id = ?", (free_slug(conn, base_slug(home, away, kickoff), ext), mid))


def free_slug(conn, base, external_id):
    """Picks the link for a match. When two matches share teams and date (rare), the one with the smaller
    stable id always gets the plain link and the other gets a short code, whatever order they arrive in."""
    holder = conn.execute("SELECT id, external_id FROM matches WHERE slug = ?", (base,)).fetchone()
    if holder is None or holder[1] == external_id:
        return base
    mine = f"{base}-{stable_id(external_id):x}"[:90]
    if stable_id(external_id) < stable_id(holder[1]):
        conn.execute("UPDATE matches SET slug = ? WHERE id = ?", (f"{base}-{stable_id(holder[1]):x}"[:90], holder[0]))
        return base
    return mine


def free_id(conn, external_id):
    """The stable id for this match, nudged upwards in the (very rare) case another match already has it."""
    n = stable_id(external_id)
    while True:
        row = conn.execute("SELECT external_id FROM matches WHERE id = ?", (n,)).fetchone()
        if row is None or row[0] == external_id:
            return n
        n += 1