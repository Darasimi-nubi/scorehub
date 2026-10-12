"""API-Football (api-sports.io) client: goal scorers and line-ups for the match page.

The free plan allows 100 requests a day and only yesterday/today/tomorrow, so
everything here is cached and a daily budget stops us going over.
"""
import difflib
import re
import time
import unicodedata
import html
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://v3.football.api-sports.io"
DAILY_LIMIT = 90  # stay under the free plan's 100 requests a day

# football-data.org competition name (as stored in our database) -> API-Football league id
LEAGUE_IDS = {
    "Premier League": 39, "Primera Division": 140, "La Liga": 140, "Serie A": 135,
    "Bundesliga": 78, "Ligue 1": 61, "UEFA Champions League": 2, "Champions League": 2,
    "Europa League": 3, "Championship": 40, "Eredivisie": 88, "Primeira Liga": 94,
    "Campeonato Brasileiro Série A": 71, "Copa Libertadores": 13,
}

# football-data.org short team names that differ a lot from API-Football's names
ALIASES = {
    "man city": "manchester city", "man united": "manchester united", "spurs": "tottenham",
    "wolves": "wolverhampton wanderers", "forest": "nottingham forest",
    "bremen": "werder bremen", "dortmund": "borussia dortmund",
    "gladbach": "borussia monchengladbach", "leverkusen": "bayer leverkusen",
    "paris": "paris saint germain", "psg": "paris saint germain",
    "mineiro": "atletico mg", "paranaense": "athletico paranaense", "qpr": "queens park rangers",
}
STOP = {"fc", "cf", "afc", "ac", "sc", "cd", "club", "the", "de", "ca"}

_calls = {"day": None, "n": 0}
_fixtures_cache = {}   # "YYYY-MM-DD" -> (fetched_at, [fixtures])
_id_cache = {}         # our external_id -> API-Football fixture
_detail_cache = {}     # our external_id -> (fetched_at, detail)

def _unescape(obj):
    """API-Football sends some names HTML-encoded (O&apos;Reilly); decode them."""
    if isinstance(obj, str):
        return html.unescape(obj)
    if isinstance(obj, list):
        return [_unescape(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _unescape(v) for k, v in obj.items()}
    return obj

def _get(path, key, **params):
    """GET with retries (some networks drop the secure connection) and a daily request budget."""
    today = datetime.now(timezone.utc).date()
    if _calls["day"] != today:
        _calls.update(day=today, n=0)
    if _calls["n"] >= DAILY_LIMIT:
        print("api-football: daily request budget used up")
        return None
    for attempt in range(4):
        try:
            resp = requests.get(BASE + path, headers={"x-apisports-key": key, "Connection": "close"},
                                params=params, timeout=15)
            _calls["n"] += 1
            data = resp.json()
            if data.get("errors"):
                print(f"api-football: {path} {params}: {data['errors']}")
                return None
            return _unescape(data)
        except Exception as exc:
            print(f"api-football: {path} attempt {attempt + 1} failed: {exc}")
            time.sleep(1)
    return None


def _norm(name):
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name.replace("-", " "))
    name = " ".join(w for w in name.split() if w not in STOP)
    return ALIASES.get(name, name)


def _similar(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if len(a) >= 3 and len(b) >= 3 and (a in b or b in a):
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _fixtures_for(day, key):
    cached = _fixtures_cache.get(day)
    if cached and time.time() - cached[0] < 600:
        return cached[1]
    data = _get("/fixtures", key, date=day)
    fixtures = data["response"] if data else (cached[1] if cached else [])
    _fixtures_cache[day] = (time.time(), fixtures)
    return fixtures


def _find_fixture(m, key):
    """Match one of our rows to an API-Football fixture by league, kickoff time and team names."""
    cached = _id_cache.get(m["external_id"])
    if cached:
        return cached
    kick = datetime.fromisoformat(m["kickoff"])
    lid = LEAGUE_IDS.get(m["league"])
    best, best_score = None, 0.0
    candidates = []
    for f in _fixtures_for(kick.astimezone(timezone.utc).date().isoformat(), key):
        if lid and f["league"]["id"] != lid:
            continue
        if abs(f["fixture"]["timestamp"] - kick.timestamp()) > 20 * 60:
            continue
        score = (_similar(m["home"], f["teams"]["home"]["name"]) +
                 _similar(m["away"], f["teams"]["away"]["name"])) / 2
        candidates.append((score, f))
    for score, f in candidates:
        if score > best_score:
            best, best_score = f, score
    ok = best is not None and (best_score >= 0.7 or (lid and len(candidates) == 1 and best_score >= 0.3))
    if ok:
        _id_cache[m["external_id"]] = best
        return best
    return None


def _lineups(items, home_id, away_id):
    out = {"home": None, "away": None}

    def players(rows):
        return [{"name": (r.get("player") or {}).get("name") or "Unknown",
                 "number": (r.get("player") or {}).get("number"),
                 "position": (r.get("player") or {}).get("pos")} for r in rows or []]

    for item in items or []:
        side = "home" if (item.get("team") or {}).get("id") == home_id else "away"
        out[side] = {"formation": item.get("formation"), "coach": (item.get("coach") or {}).get("name"),
                     "starters": players(item.get("startXI")), "bench": players(item.get("substitutes"))}
    empty = {"formation": None, "coach": None, "starters": [], "bench": []}
    out = {k: v or empty for k, v in out.items()}
    return out if out["home"]["starters"] or out["away"]["starters"] else None


FAIL = object()  # a request failed: keep whatever we had and retry later

# (API-Football stat name, label shown on the page)
STAT_ROWS = [
    ("Ball Possession", "Possession"), ("expected_goals", "Expected goals (xG)"),
    ("Total Shots", "Shots"), ("Shots on Goal", "Shots on target"),
    ("Corner Kicks", "Corners"), ("Fouls", "Fouls"), ("Offsides", "Offsides"),
    ("Yellow Cards", "Yellow cards"), ("Red Cards", "Red cards"),
    ("Goalkeeper Saves", "Saves"), ("Passes %", "Pass accuracy"),
]


def _number(value):
    try:
        return float(str(value).replace("%", "")) if value is not None else 0.0
    except ValueError:
        return 0.0


def _stats(items, home_id):
    """Turn API-Football's two statistics lists into rows with bar widths for the page."""
    sides = {"home": {}, "away": {}}
    for item in items or []:
        side = "home" if (item.get("team") or {}).get("id") == home_id else "away"
        sides[side] = {r.get("type"): r.get("value") for r in item.get("statistics") or []}
    rows = []
    for key, label in STAT_ROWS:
        hv, av = sides["home"].get(key), sides["away"].get(key)
        if hv is None and av is None:
            continue
        h, a = _number(hv), _number(av)
        total = h + a
        rows.append({
            "label": label,
            "home": "-" if hv is None else hv, "away": "-" if av is None else av,
            "home_pct": round(h / total * 100) if total else 50,
            "away_pct": round(a / total * 100) if total else 50,
            "lead": "home" if h > a else "away" if a > h else "",
        })
    return rows


def _cached(ext, part, ttl, loader):
    """Per-part cache so one match page never re-requests data that can't have changed."""
    store = _detail_cache.setdefault(ext, {})
    entry = store.get(part)
    if entry and time.time() - entry[0] < ttl:
        return entry[1]
    value = loader()
    if value is FAIL:
        return entry[1] if entry else None
    store[part] = (time.time(), value)
    return value


def fetch_detail(m, key):
    """Goals, stats, line-ups, venue and referee for one match row, or None when unavailable."""
    if m["status"] == "postponed":
        return None
    now = datetime.now(timezone.utc)
    kick = datetime.fromisoformat(m["kickoff"])
    # the free plan only covers yesterday .. tomorrow (UTC)
    if abs((kick.astimezone(timezone.utc).date() - now.date()).days) > 1:
        return None
    if kick - now > timedelta(hours=2):
        return None  # line-ups are only published about an hour before kickoff
    fx = _find_fixture(m, key)
    if not fx:
        return None

    ext, status = m["external_id"], m["status"]
    fid, home_id, away_id = fx["fixture"]["id"], fx["teams"]["home"]["id"], fx["teams"]["away"]["id"]
    played = status in ("live", "finished")
    final = 6 * 3600  # a finished match won't change, so keep it for hours

    def load_goals():
        data = _get("/fixtures/events", key, fixture=fid)
        if data is None:
            return FAIL
        goals = []
        for e in data.get("response", []):
            if e.get("type") != "Goal" or e.get("detail") == "Missed Penalty":
                continue
            t = e.get("time") or {}
            label = f"{t['elapsed']}'" if t.get("elapsed") is not None else ""
            if label and t.get("extra"):
                label = f"{t['elapsed']}+{t['extra']}'"
            goals.append({
                "minute": label,
                "player": (e.get("player") or {}).get("name") or "Unknown",
                "kind": {"Penalty": "pen", "Own Goal": "og"}.get(e.get("detail"), ""),
                "side": "home" if (e.get("team") or {}).get("id") == home_id else "away",
            })
        return goals

    def load_stats():
        data = _get("/fixtures/statistics", key, fixture=fid)
        return FAIL if data is None else _stats(data.get("response"), home_id)

    def load_lineups():
        data = _get("/fixtures/lineups", key, fixture=fid)
        return FAIL if data is None else _lineups(data.get("response"), home_id, away_id)

    goals = _cached(ext, "goals", final if status == "finished" else 90, load_goals) if played else []
    stats = _cached(ext, "stats", final if status == "finished" else 180, load_stats) if played else []
    lineups = _cached(ext, "lineups", 6 * 3600, load_lineups)
    if not lineups:  # not published yet (or none for this league): look again in a few minutes
        _detail_cache[ext]["lineups"] = (time.time() - 6 * 3600 + 300, None)

    return {
        "goals": goals or [], "stats": stats or [], "lineups": lineups,
        "venue": (fx["fixture"].get("venue") or {}).get("name"),
        "referee": fx["fixture"].get("referee"),
    }