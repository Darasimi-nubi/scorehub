"""Match highlights: finds an official highlights video on YouTube for a finished match.

Needs a free YouTube Data API key in the YOUTUBE_API_KEY variable (Railway Variables / .env).
Without it the Highlights tab still appears, with a "search on YouTube" button instead of a player.

Each search costs 100 of the free 10,000 daily quota units, so a match is searched at most
every 45 minutes (12 hours after the first two days) and the result is remembered in a small
SQLite file. Searches run in a background thread so a match page never waits for YouTube.
"""
import os
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_subs = os.environ.get("SUBS_DB_PATH")
# Keep the cache next to the push subscriptions, so it survives deploys if that is on a volume.
DB_PATH = os.environ.get("HIGHLIGHTS_DB_PATH") or os.path.join(os.path.dirname(_subs) if _subs else BASE_DIR, "highlights.db")

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
COUNTRY = (os.environ.get("HIGHLIGHTS_COUNTRY") or "NG").upper()  # where most of your visitors are
WAIT_AFTER_KICKOFF = timedelta(hours=3)   # highlights are rarely up before this
RETRY_EARLY = 45 * 60                     # seconds between searches, first two days
RETRY_LATE = 12 * 3600                    # ...then until the give-up point
EARLY_WINDOW = timedelta(days=2)
GIVE_UP_AFTER = timedelta(days=14)

_lock = threading.Lock()
_inflight = set()
_backoff = {}  # key -> time until which a failed search is not retried
ERROR_PAUSE = 300
_quota = {"day": None, "used": 0, "blocked_until": 0.0}

STOP_WORDS = {"fc", "cf", "afc", "sc", "ac", "as", "ssc", "cd", "ud", "sv", "fk", "bk", "the", "club", "de", "del", "da",
              "of", "and", "u21", "u23", "u19", "women"}
BAD_TITLE = ("preview", "prediction", "predictions", "reaction", "reacts", "watchalong", "watch along", "simulation",
             "efootball", "fifa", "ea sports fc", "ea fc", "pes ", "gameplay", "press conference", "live stream",
             "livestream", "live:", "pre-match", "pre match", "post match", "post-match", "interview", "podcast")
GOOD_TITLE = ("highlight", "highlights", "goals", "resumen", "resume", "résumé", "extended", "all goals", "best moments")
TRUSTED = ("premier league", "laliga", "la liga", "serie a", "bundesliga", "ligue 1", "uefa", "champions league",
           "europa league", "conference league", "mls", "nba", "nhl", "wnba", "espn", "sky sports", "tnt sports",
           "bein", "supersport", "cbs sports", "dazn", "fox", "nbc sports", "atp", "wta", "icc", "ipl", "bbc",
           "itv", "canal+", "rmc", "movistar", "fifa", "caf", "concacaf", "conmebol", "copa", "fa cup", "efl",
           "sportv", "tsn", "sony sports", "ten sports", "wimbledon", "us open", "roland", "australian open")


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS highlights (
        key TEXT PRIMARY KEY, video_id TEXT, title TEXT, channel TEXT, checked REAL NOT NULL)""")
    return conn


def _key(m):
    return "v2|" + (m["external_id"] or f"id-{m['id']}")  # v2: results saved before the country check are ignored


def _kickoff(m):
    dt = datetime.fromisoformat(m["kickoff"])
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", text)


def _tokens(team):
    words = [w for w in _norm(team).split() if w not in STOP_WORDS and len(w) >= 3]
    return words or [w for w in _norm(team).split() if len(w) >= 3]


def _coverage(tokens, title_words):
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if t in title_words) / len(tokens)


def search_url(m):
    return "https://www.youtube.com/results?search_query=" + quote_plus(f"{m['home']} vs {m['away']} highlights")


def _quota_ok():
    today = datetime.now(timezone.utc).date().isoformat()
    if _quota["day"] != today:
        _quota.update(day=today, used=0)
    limit = int(os.environ.get("YOUTUBE_DAILY_SEARCHES") or 90)
    return _quota["used"] < limit and time.time() >= _quota["blocked_until"]


def _search(m, key):
    """One YouTube search; returns (video_id, title, channel) or None."""
    kickoff = _kickoff(m)
    params = {
        "part": "snippet", "type": "video", "maxResults": 15, "order": "relevance",
        "videoEmbeddable": "true", "relevanceLanguage": "en", "key": key, "regionCode": COUNTRY,
        "q": f"{m['home']} vs {m['away']} highlights",
        "publishedAfter": kickoff.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _quota["used"] += 1
    resp = requests.get(SEARCH_URL, params=params, timeout=12)
    if resp.status_code == 403 and "quota" in resp.text.lower():
        _quota["blocked_until"] = time.time() + 3 * 3600
        print("highlights: YouTube quota used up; pausing searches for 3 hours")
        raise RuntimeError("quota")
    if not resp.ok:
        print(f"highlights: YouTube {resp.status_code} {resp.text[:160]}")
        raise RuntimeError("api")
    home_t, away_t = _tokens(m["home"]), _tokens(m["away"])
    candidates = []
    for item in resp.json().get("items") or []:
        sn = item.get("snippet") or {}
        title, channel = sn.get("title") or "", sn.get("channelTitle") or ""
        low = _norm(title)
        if any(b in (title.lower() + " ") for b in BAD_TITLE):
            continue
        words = set(low.split())
        hc, ac = _coverage(home_t, words), _coverage(away_t, words)
        if hc < 0.5 or ac < 0.5:
            continue  # the title must name both teams
        if not any(g in title.lower() for g in GOOD_TITLE):
            continue
        score = hc + ac + 1.0
        if any(t in channel.lower() for t in TRUSTED):
            score += 2.0
        if "highlights" in title.lower():
            score += 0.5
        candidates.append((score, item["id"]["videoId"], title, channel))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    top = candidates[:5]
    try:
        top = _check_playable(top, key)
    except Exception as exc:
        print(f"highlights: could not check availability: {exc}")  # fall back to the best title match
    if not top:
        return None
    _score, vid, title, channel = top[0]
    return vid, title, channel


def _check_playable(top, key):
    """Drops videos that cannot be embedded or are blocked in COUNTRY, and prefers ones with no
    country restrictions at all (they play for every visitor). Costs 1 quota unit."""
    resp = requests.get(VIDEOS_URL, params={"part": "contentDetails,status", "key": key,
                                            "id": ",".join(c[1] for c in top)}, timeout=12)
    if not resp.ok:
        raise RuntimeError(f"videos.list {resp.status_code}")
    info = {v["id"]: v for v in resp.json().get("items") or []}
    scored = []
    for score, vid, title, channel in top:
        v = info.get(vid)
        if v is None:
            continue  # gone, or private
        if not (v.get("status") or {}).get("embeddable", True):
            continue
        rr = (v.get("contentDetails") or {}).get("regionRestriction") or {}
        if COUNTRY in (rr.get("blocked") or []):
            continue
        if "allowed" in rr and COUNTRY not in rr["allowed"]:
            continue
        if not rr:
            score += 1.0
        scored.append((score, vid, title, channel))
    scored.sort(reverse=True)
    return scored


def _run(m, key):
    ck = _key(m)
    try:
        result = _search(m, key)
    except Exception as exc:
        if str(exc) not in ("quota", "api"):
            print(f"highlights: search failed: {exc}")
        result = "error"
    finally:
        with _lock:
            _inflight.discard(ck)
    if result == "error":
        _backoff[ck] = time.time() + ERROR_PAUSE  # nothing stored; try again in a few minutes
        return
    with _db() as conn:
        vid, title, channel = result if result else (None, None, None)
        conn.execute("INSERT OR REPLACE INTO highlights (key, video_id, title, channel, checked) VALUES (?,?,?,?,?)",
                     (ck, vid, title, channel, time.time()))


def get(m):
    """State for the Highlights tab: None for matches that are not finished, otherwise a dict with
    state = found | searching | wait | none | error | nokey, plus video_id/title/channel and a YouTube search link."""
    if m["status"] != "finished":
        return None
    info = {"state": "nokey", "video_id": None, "title": None, "channel": None, "search_url": search_url(m)}
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return info
    now = datetime.now(timezone.utc)
    try:
        kickoff = _kickoff(m)
    except Exception:
        return info
    ck = _key(m)
    with _db() as conn:
        row = conn.execute("SELECT video_id, title, channel, checked FROM highlights WHERE key = ?", (ck,)).fetchone()
    if row and row[0]:
        info.update(state="found", video_id=row[0], title=row[1], channel=row[2])
        return info
    if now < kickoff + WAIT_AFTER_KICKOFF:
        info["state"] = "wait"
        return info
    if now > kickoff + GIVE_UP_AFTER:
        info["state"] = "none"
        return info
    if _backoff.get(ck, 0) > time.time():
        info["state"] = "error"
        return info
    retry = RETRY_EARLY if now < kickoff + EARLY_WINDOW else RETRY_LATE
    if row and time.time() - row[3] < retry:
        info["state"] = "none"
        return info
    with _lock:
        busy = ck in _inflight
        if not busy and _quota_ok():
            _inflight.add(ck)
            threading.Thread(target=_run, args=(m, api_key), daemon=True).start()
            busy = True
    info["state"] = "searching" if busy else "none"
    return info