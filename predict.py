"""Match predictions: pick home win, draw or away win before kick-off, earn points when you're right.

Votes live in a small SQLite file. On Railway the main database is rebuilt on every deploy, so point
PREDICT_DB_PATH (or SUBS_DB_PATH, whose folder is reused) at a volume to keep votes and the leaderboard.

Visitors have no accounts: the browser makes a random id and keeps it. A nickname is only needed to appear on the
leaderboard. This is a game for fun, so clearing your browser data starts you from zero.
"""
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_subs = os.environ.get("SUBS_DB_PATH")
DB_PATH = os.environ.get("PREDICT_DB_PATH") or os.path.join(os.path.dirname(_subs) if _subs else BASE_DIR, "predict.db")
POINTS = 3
PICKS = ("H", "D", "A")
UID_RE = re.compile(r"^[a-f0-9]{16,40}$")
NICK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{2,19}$")

_lock = threading.Lock()


def _db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS users (uid TEXT PRIMARY KEY, nick TEXT UNIQUE COLLATE NOCASE, created REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS votes (match_id INTEGER NOT NULL, uid TEXT NOT NULL, pick TEXT NOT NULL,"
                " kickoff TEXT NOT NULL, ts REAL, outcome TEXT, points INTEGER, PRIMARY KEY (match_id, uid))")
    con.execute("CREATE INDEX IF NOT EXISTS idx_votes_uid ON votes (uid)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_votes_open ON votes (outcome, kickoff)")
    return con


def valid_uid(uid):
    return isinstance(uid, str) and bool(UID_RE.match(uid))


def outcome_of(row):
    """H, D or A for a finished match with a score, else None."""
    if row.get("status") != "finished" or row.get("home_score") is None or row.get("away_score") is None:
        return None
    h, a = int(row["home_score"]), int(row["away_score"])
    return "H" if h > a else "A" if a > h else "D"


def can_vote(row, now=None):
    now = now or datetime.now(timezone.utc)
    if row.get("sport") != "football" or row.get("status") != "scheduled":
        return False
    return datetime.fromisoformat(row["kickoff"]) > now


def vote(match_id, uid, pick, row):
    """Returns (ok, reason)."""
    if not valid_uid(uid) or pick not in PICKS:
        return False, "bad"
    if not can_vote(row):
        return False, "closed"
    with _lock, _db() as con:
        con.execute("INSERT INTO votes (match_id, uid, pick, kickoff, ts) VALUES (?,?,?,?,?)"
                    " ON CONFLICT(match_id, uid) DO UPDATE SET pick=excluded.pick, ts=excluded.ts WHERE outcome IS NULL",
                    (match_id, uid, pick, row["kickoff"], time.time()))
    return True, None


def tally(match_id, uid=None):
    """{"H": n, "D": n, "A": n, "total": n, "mine": pick or None}"""
    with _lock, _db() as con:
        counts = dict(con.execute("SELECT pick, COUNT(*) FROM votes WHERE match_id = ? GROUP BY pick", (match_id,)).fetchall())
        mine = None
        if valid_uid(uid):
            r = con.execute("SELECT pick FROM votes WHERE match_id = ? AND uid = ?", (match_id, uid)).fetchone()
            mine = r[0] if r else None
    out = {p: int(counts.get(p, 0)) for p in PICKS}
    out["total"] = sum(out[p] for p in PICKS)
    out["mine"] = mine
    return out


def set_nick(uid, nick):
    """Returns (ok, reason)."""
    nick = " ".join((nick or "").split())
    if not valid_uid(uid) or not NICK_RE.match(nick):
        return False, "format"
    try:
        with _lock, _db() as con:
            con.execute("INSERT INTO users (uid, nick, created) VALUES (?,?,?)"
                        " ON CONFLICT(uid) DO UPDATE SET nick=excluded.nick", (uid, nick, time.time()))
    except sqlite3.IntegrityError:
        return False, "taken"
    return True, None


def get_nick(uid):
    if not valid_uid(uid):
        return None
    with _lock, _db() as con:
        r = con.execute("SELECT nick FROM users WHERE uid = ?", (uid,)).fetchone()
    return r[0] if r else None


def settle(rows):
    """Score every open vote for matches in `rows` that have finished. Safe to call as often as you like."""
    finished = {r["id"]: outcome_of(r) for r in rows if outcome_of(r)}
    if not finished:
        return 0
    n = 0
    with _lock, _db() as con:
        open_ids = {r[0] for r in con.execute(
            "SELECT DISTINCT match_id FROM votes WHERE outcome IS NULL AND match_id IN (%s)" % ",".join("?" * len(finished)),
            list(finished)).fetchall()}
        for mid in open_ids:
            out = finished[mid]
            n += con.execute("UPDATE votes SET outcome = ?, points = CASE WHEN pick = ? THEN ? ELSE 0 END"
                             " WHERE match_id = ? AND outcome IS NULL", (out, out, POINTS, mid)).rowcount
    return n


def leaderboard(days=None, limit=50):
    """Rows of (nick, points, correct, played), best first. Only scored votes from people with a nickname count."""
    sql = ("SELECT u.nick, SUM(v.points), SUM(v.points > 0), COUNT(*) FROM votes v JOIN users u ON u.uid = v.uid"
           " WHERE v.outcome IS NOT NULL")
    params = []
    if days:
        sql += " AND v.kickoff >= ?"
        params.append((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    sql += " GROUP BY u.uid ORDER BY SUM(v.points) DESC, SUM(v.points > 0) DESC, COUNT(*) ASC, u.nick LIMIT ?"
    params.append(limit)
    with _lock, _db() as con:
        return con.execute(sql, params).fetchall()


def my_stats(uid):
    if not valid_uid(uid):
        return None
    with _lock, _db() as con:
        r = con.execute("SELECT COALESCE(SUM(points),0), COALESCE(SUM(points > 0),0), COUNT(*) FROM votes"
                        " WHERE uid = ? AND outcome IS NOT NULL", (uid,)).fetchone()
        pending = con.execute("SELECT COUNT(*) FROM votes WHERE uid = ? AND outcome IS NULL", (uid,)).fetchone()[0]
    return {"points": r[0], "correct": r[1], "played": r[2], "pending": pending}