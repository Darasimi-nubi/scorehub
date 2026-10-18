"""API-Football (api-sports.io) client: goal scorers and line-ups for the match page.

The free plan allows 100 requests a day and only yesterday/today/tomorrow, so
everything here is cached and a daily budget stops us going over.
"""
import difflib
import json
import html
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://v3.football.api-sports.io"
# Free plan: 100 requests a day. On a paid plan, raise these with Railway variables.
DAILY_LIMIT = int(os.environ.get("API_FOOTBALL_DAILY_LIMIT", "95"))   # everything combined
LIVE_LIMIT = int(os.environ.get("API_FOOTBALL_LIVE_LIMIT", "50"))     # of which live-score refreshes
LIVE_TTL = int(os.environ.get("API_FOOTBALL_LIVE_TTL", "150"))        # seconds between live-score refreshes

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

_calls = {"day": None, "n": 0, "live": 0}
_live = {"at": 0.0, "ok_at": 0.0, "rows": []}  # last live-scores list
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


def _roll_day():
    today = datetime.now(timezone.utc).date()
    if _calls["day"] != today:
        _calls.update(day=today, n=0, live=0)


def _get(path, key, **params):
    """GET with retries (some networks drop the secure connection) and a daily request budget."""
    _roll_day()
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
                if "request limit" in json.dumps(data["errors"]).lower():
                    _calls["n"] = DAILY_LIMIT  # the provider says today's allowance is gone: stop asking
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
    # a failed lookup is retried after a minute instead of being remembered for ten
    _fixtures_cache[day] = (time.time() if data else time.time() - 540, fixtures)
    return fixtures


def _best_match(m, fixtures):
    """Pick the API-Football fixture for one of our rows by league, kickoff time and team names."""
    kick = datetime.fromisoformat(m["kickoff"])
    lid = LEAGUE_IDS.get(m["league"])
    candidates = []
    for f in fixtures:
        if lid and f["league"]["id"] != lid:
            continue
        if abs(f["fixture"]["timestamp"] - kick.timestamp()) > 20 * 60:
            continue
        score = (_similar(m["home"], f["teams"]["home"]["name"]) +
                 _similar(m["away"], f["teams"]["away"]["name"])) / 2
        candidates.append((score, f))
    if not candidates:
        return None
    best_score, best = max(candidates, key=lambda c: c[0])
    ok = best_score >= 0.7 or (lid and len(candidates) == 1 and best_score >= 0.3)
    return best if ok else None


def _find_fixture(m, key):
    cached = _id_cache.get(m["external_id"])
    if cached:
        return cached
    kick = datetime.fromisoformat(m["kickoff"])
    best = _best_match(m, _fixtures_for(kick.astimezone(timezone.utc).date().isoformat(), key))
    if best:
        _id_cache[m["external_id"]] = best
    return best


# ---------- real-time scores ----------
PERIODS = {"1H": "1H", "2H": "2H", "HT": "HT", "ET": "ET", "BT": "ET", "P": "PEN", "LIVE": "1H",
           "INT": "HT", "SUSP": "HT"}


def live_fixtures(key):
    """Everything in play right now, in ONE request, shared by every visitor for LIVE_TTL seconds.

    Returns (fixtures, fresh). fresh is False when the list is old (budget used up or API down),
    in which case callers should not trust it.
    """
    _roll_day()
    now = time.time()
    if now - _live["at"] >= LIVE_TTL and _calls["live"] < LIVE_LIMIT:
        _live["at"] = now  # set first, so a failing API isn't hammered
        data = _get("/fixtures", key, live="all")
        if data is not None:
            _calls["live"] += 1
            _live.update(ok_at=now, rows=data.get("response", []))
    return _live["rows"], now - _live["ok_at"] < LIVE_TTL * 3


def _maybe_live(r, now):
    """Could this row be in play right now, even if our (delayed) data hasn't caught up?"""
    if r["status"] not in ("scheduled", "live"):
        return False
    since = (now - datetime.fromisoformat(r["kickoff"])).total_seconds() / 60
    return -2 <= since <= 140


def overlay_live(rows, key):
    """Update football rows (dicts) in place with real-time status, score and minute where known."""
    now = datetime.now(timezone.utc)
    candidates = [r for r in rows if _maybe_live(r, now)]
    if not candidates:
        return rows
    fixtures, fresh = live_fixtures(key)
    if not fresh:
        return rows
    for r in candidates:
        fx = _best_match(r, fixtures)
        if not fx:
            # the live feed is fresh and this match isn't in it: if it should be over by now, it is
            since = (now - datetime.fromisoformat(r["kickoff"])).total_seconds() / 60
            if r["status"] == "live" and since >= 110:
                r["status"], r["period"], r["clock"] = "finished", "FT", None
            continue
        st = fx["fixture"]["status"]
        goals = fx.get("goals") or {}
        r["status"] = "live"
        r["home_score"], r["away_score"] = goals.get("home"), goals.get("away")
        r["period"] = PERIODS.get(st.get("short"), "1H")
        elapsed, extra = st.get("elapsed"), st.get("extra")
        if r["period"] == "HT" or elapsed is None:
            r["clock"] = None
        else:
            r["clock"] = f"{elapsed}+{extra}'" if extra else f"{elapsed}'"
    return rows


def settle_past(rows, key):
    """Fix matches our delayed feed still lists as 'scheduled' although they kicked off long ago."""
    now = datetime.now(timezone.utc)
    for r in rows:
        if r["status"] != "scheduled":
            continue
        kick = datetime.fromisoformat(r["kickoff"])
        since = (now - kick).total_seconds() / 60
        if since < 130 or since > 48 * 60:
            continue
        fx = _best_match(r, _fixtures_for(kick.astimezone(timezone.utc).date().isoformat(), key))
        short = ((fx or {}).get("fixture", {}).get("status") or {}).get("short")
        if short in ("FT", "AET", "PEN"):
            goals = fx.get("goals") or {}
            r["status"], r["period"], r["clock"] = "finished", "FT", None
            r["home_score"], r["away_score"] = goals.get("home"), goals.get("away")
        elif short in ("PST", "CANC", "ABD", "AWD", "WO"):
            r["status"] = "postponed"
    return rows


def _place(starters, side):
    """Give each starter x/y percentages on one full pitch: home on the top half, away on the bottom.

    API-Football's grid is "row:position", row 1 being the goalkeeper. Returns False when a
    player has no grid (some leagues), so the page can fall back to a plain list.
    """
    rows = {}
    try:
        for p in starters:
            row, col = (int(v) for v in p["grid"].split(":"))
            rows.setdefault(row, []).append((col, p))
    except (ValueError, AttributeError, TypeError):
        return False
    if not rows:
        return False
    lines = sorted(rows)
    for i, row in enumerate(lines):
        half_y = 7 + (i / (len(lines) - 1) * 37 if len(lines) > 1 else 0)  # 7% (keeper) .. 44% (striker)
        players = sorted(rows[row], key=lambda cp: cp[0])
        for k, (_, p) in enumerate(players):
            x = 50 if len(players) == 1 else 13 + k / (len(players) - 1) * 74
            p["x"] = round(100 - x if side == "home" else x, 1)  # home faces down, so mirror left/right
            p["y"] = round(half_y if side == "home" else 100 - half_y, 1)
    return True


POSITIONS = {"G": "Goalkeeper", "D": "Defender", "M": "Midfielder", "F": "Forward"}


def _lineups(items, home_id, away_id):
    out = {"home": None, "away": None}

    def players(rows):
        return [{"id": (r.get("player") or {}).get("id"),
                 "name": (r.get("player") or {}).get("name") or "Unknown",
                 "number": (r.get("player") or {}).get("number"),
                 "position": POSITIONS.get((r.get("player") or {}).get("pos"), (r.get("player") or {}).get("pos")),
                 "grid": (r.get("player") or {}).get("grid"),
                 "photo": f"https://media.api-sports.io/football/players/{(r.get('player') or {}).get('id')}.png"}
                for r in rows or []]

    for item in items or []:
        side = "home" if (item.get("team") or {}).get("id") == home_id else "away"
        out[side] = {"formation": item.get("formation"), "coach": (item.get("coach") or {}).get("name"),
                     "starters": players(item.get("startXI")), "bench": players(item.get("substitutes"))}
    empty = {"formation": None, "coach": None, "starters": [], "bench": []}
    out = {k: v or empty for k, v in out.items()}
    if not (out["home"]["starters"] or out["away"]["starters"]):
        return None
    out["pitch"] = bool(out["home"]["starters"] and out["away"]["starters"]) and \
        _place(out["home"]["starters"], "home") and _place(out["away"]["starters"], "away")
    return out


def _analyse(events, lineups, home_id):
    """Per-player marks (goals, cards, off/on) plus the substitutions list for each side."""
    people = {str(p["id"]): p for s in ("home", "away") for p in lineups[s]["starters"] + lineups[s]["bench"]}
    starters = {str(p["id"]) for s in ("home", "away") for p in lineups[s]["starters"]}
    came_on = set()
    marks, subs = {}, {"home": [], "away": []}

    def mark(pid):
        return marks.setdefault(str(pid), {"goals": 0, "og": 0, "yellow": 0, "red": 0, "off": None, "on": None})

    def person(pid, raw):
        p = people.get(str(pid)) or {}
        return {"name": p.get("name") or raw.get("name") or "Unknown", "number": p.get("number")}

    for e in events:
        kind, detail = e.get("type"), e.get("detail") or ""
        t = e.get("time") or {}
        label = (f"{t['elapsed']}+{t['extra']}'" if t.get("extra") else f"{t['elapsed']}'") \
            if t.get("elapsed") is not None else ""
        order = (t.get("elapsed") or 0) * 100 + (t.get("extra") or 0)
        pa = e.get("player") or {}
        pid = pa.get("id")
        if kind == "Goal" and pid and detail != "Missed Penalty":
            mark(pid)["og" if detail == "Own Goal" else "goals"] += 1
        elif kind == "Card" and pid:
            mark(pid)["red" if "Red" in detail or "Second" in detail else "yellow"] = 1
        elif kind == "subst":
            pb = e.get("assist") or {}
            a, b = pa.get("id"), pb.get("id")
            if not a or not b:
                continue
            # whoever started (or already came on) is the one leaving, whichever field the API puts them in
            if str(b) in starters or str(b) in came_on:
                a, b, pa, pb = b, a, pb, pa
            mark(a)["off"], mark(b)["on"] = label, label
            came_on.add(str(b))
            side = "home" if (e.get("team") or {}).get("id") == home_id else "away"
            subs[side].append({"minute": label, "order": order, "in": person(b, pb), "out": person(a, pa)})
    for side in subs:
        subs[side].sort(key=lambda s: s["order"])
    return marks, subs


def _timeline(events, lineups, home_id, status):
    """Chronological match events (goals, cards, subs, VAR) with running score and HT / FT dividers."""
    starters, came_on = set(), set()
    if lineups:
        starters = {str(p["id"]) for s in ("home", "away") for p in lineups[s]["starters"]}
    rows, score = [], [0, 0]
    def when(e):
        t = e.get("time") or {}
        return (t.get("elapsed") or 0) * 100 + (t.get("extra") or 0)
    for e in sorted(events or [], key=when):
        kind, detail = e.get("type"), e.get("detail") or ""
        t = e.get("time") or {}
        if t.get("elapsed") is None:
            continue
        label = f"{t['elapsed']}+{t['extra']}'" if t.get("extra") else f"{t['elapsed']}'"
        order = t["elapsed"] * 100 + (t.get("extra") or 0)
        side = "home" if (e.get("team") or {}).get("id") == home_id else "away"
        pa, pb = e.get("player") or {}, e.get("assist") or {}
        row = {"order": order, "elapsed": t["elapsed"], "minute": label, "side": side,
               "kind": "", "player": pa.get("name") or "", "other": "", "score": "", "note": ""}
        if kind == "Goal":
            if detail == "Missed Penalty":
                row.update(kind="pen-miss", note="Missed penalty")
            else:
                score[0 if side == "home" else 1] += 1
                row.update(kind="goal", other=pb.get("name") or "", score=f"{score[0]} - {score[1]}",
                           note={"Penalty": "PEN", "Own Goal": "OG"}.get(detail, ""))
        elif kind == "Card":
            row["kind"] = "red" if ("Red" in detail or "Second" in detail) else "yellow"
        elif kind == "subst":
            a, b = pa, pb
            if str(b.get("id")) in starters or str(b.get("id")) in came_on:
                a, b = b, a
            came_on.add(str(b.get("id")))
            row.update(kind="sub", player=b.get("name") or "", other=a.get("name") or "")
        elif kind == "Var":
            row.update(kind="var", note=detail)
        else:
            continue
        if row["player"] or row["kind"] == "var":
            rows.append(row)
    rows.sort(key=lambda r: r["order"])

    out, ht_done = [], False
    def divider(label):
        out.append({"kind": "divider", "minute": label, "score": f"{score[0]} - {score[1]}"})
    for r in rows:
        if not ht_done and r["elapsed"] > 45:
            ht_done = True
            # score at the break = score after the last first-half event
            first = [x for x in out if x.get("score")]
            if first:
                score_ht = first[-1]["score"]
            else:
                score_ht = "0 - 0"
            out.append({"kind": "divider", "minute": "HT", "score": score_ht})
        out.append(r)
    if status == "finished":
        if not ht_done:
            first = [x for x in out if x.get("score")]
            out.append({"kind": "divider", "minute": "HT", "score": first[-1]["score"] if first else "0 - 0"})
        divider("FT")
    return out


def _h2h(items, home_id, away_id, skip_fid=None):
    """Past meetings of the two clubs, newest first, plus a win / draw / win tally."""
    done = {"FT", "AET", "PEN"}
    meetings, tally = [], {"home": 0, "draw": 0, "away": 0}
    for f in sorted(items or [], key=lambda f: f["fixture"]["date"], reverse=True):
        if f["fixture"].get("id") == skip_fid or (f["fixture"].get("status") or {}).get("short") not in done:
            continue  # not the match being viewed, and only finished games
        hg, ag = f["goals"]["home"], f["goals"]["away"]
        if hg is None or ag is None:
            continue
        winner = f["teams"]["home"]["id"] if hg > ag else f["teams"]["away"]["id"] if ag > hg else None
        tally["draw" if winner is None else "home" if winner == home_id else "away"] += 1
        meetings.append({"date": f["fixture"]["date"][:10], "league": (f.get("league") or {}).get("name", ""),
                         "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                         "hs": hg, "as": ag, "home_won": winner == f["teams"]["home"]["id"],
                         "away_won": winner == f["teams"]["away"]["id"]})
    return {"tally": tally, "meetings": meetings[:10]} if meetings else None


def _tone(rating):
    """SofaScore-style colour band for a rating."""
    return ("blue" if rating >= 9 else "green" if rating >= 7 else "yellow" if rating >= 6.5
            else "orange" if rating >= 6 else "red")


def _ratings(items):
    """Player id -> {rating, tone} from /fixtures/players (only players who have a rating)."""
    out = {}
    for team in items or []:
        for p in team.get("players") or []:
            stats = (p.get("statistics") or [{}])[0]
            raw = (stats.get("games") or {}).get("rating")
            try:
                value = round(float(raw), 1)
            except (TypeError, ValueError):
                continue
            out[str((p.get("player") or {}).get("id"))] = {"rating": f"{value:.1f}", "tone": _tone(value), "value": value}
    return out


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


def budget_used_up():
    _roll_day()
    return _calls["n"] >= DAILY_LIMIT


def _notice(text):
    return {"goals": [], "stats": [], "lineups": None, "marks": {}, "subs": {"home": [], "away": []},
            "h2h": None, "timeline": [], "venue": None, "referee": None, "ratings": {}, "motm": None, "notice": text}


def fetch_detail(m, key):
    """Goals, stats, line-ups, venue and referee for one match row, or None when unavailable."""
    if m["status"] == "postponed":
        return None
    now = datetime.now(timezone.utc)
    kick = datetime.fromisoformat(m["kickoff"])
    # the free plan only covers yesterday .. tomorrow (UTC)
    if abs((kick.astimezone(timezone.utc).date() - now.date()).days) > 1:
        return None
    soon = kick - now <= timedelta(hours=2)  # line-ups only appear about an hour before kickoff
    fx = _find_fixture(m, key)
    if not fx:
        if budget_used_up():
            return _notice("Match details are paused: today's data allowance from the stats provider "
                           "is used up. They come back after midnight UTC (1 am in Lagos).")
        return _notice("No detailed data is available for this match yet.")

    ext, status = m["external_id"], m["status"]
    fid, home_id, away_id = fx["fixture"]["id"], fx["teams"]["home"]["id"], fx["teams"]["away"]["id"]
    played = status in ("live", "finished")
    if not soon:
        played = False
    final = 6 * 3600  # a finished match won't change, so keep it for hours

    def load_goals():
        data = _get("/fixtures/events", key, fixture=fid)
        if data is None:
            return FAIL
        events = data.get("response", [])
        goals = []
        for e in events:
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
        return {"goals": goals, "events": events}

    def load_stats():
        data = _get("/fixtures/statistics", key, fixture=fid)
        return FAIL if data is None else _stats(data.get("response"), home_id)

    def load_lineups():
        data = _get("/fixtures/lineups", key, fixture=fid)
        return FAIL if data is None else _lineups(data.get("response"), home_id, away_id)

    events = _cached(ext, "goals", final if status == "finished" else 120, load_goals) if played else None
    stats = _cached(ext, "stats", final if status == "finished" else 300, load_stats) if played else []
    lineups = None
    if soon:
        lineups = _cached(ext, "lineups", 6 * 3600, load_lineups)
        if not lineups:  # not published yet (or none for this league): look again in a few minutes
            _detail_cache[ext]["lineups"] = (time.time() - 6 * 3600 + 300, None)

    def load_h2h():
        data = _get("/fixtures/headtohead", key, h2h=f"{home_id}-{away_id}")  # no "last": the free plan refuses it
        return FAIL if data is None else _h2h(data.get("response"), home_id, away_id, fid)

    def load_players():
        data = _get("/fixtures/players", key, fixture=fid)
        return FAIL if data is None else _ratings(data.get("response"))

    ratings = {}
    if lineups and played:
        ratings = _cached(ext, "players", final if status == "finished" else 600, load_players) or {}
    motm = None
    if ratings and status == "finished":
        motm = max(ratings, key=lambda k: ratings[k]["value"])

    h2h = _cached(ext, "h2h", 12 * 3600, load_h2h)
    if not h2h:  # failed or none: don't ask again for half an hour
        _detail_cache[ext]["h2h"] = (time.time() - 12 * 3600 + 1800, None)

    marks, subs = _analyse((events or {}).get("events", []), lineups, home_id) if lineups \
        else ({}, {"home": [], "away": []})
    return {
        "goals": (events or {}).get("goals", []), "stats": stats or [], "lineups": lineups,
        "marks": marks, "subs": subs, "h2h": h2h, "ratings": ratings, "motm": motm,
        "timeline": _timeline((events or {}).get("events", []), lineups, home_id, status) if events else [],
        "venue": (fx["fixture"].get("venue") or {}).get("name"),
        "referee": fx["fixture"].get("referee"),
        "notice": ("Some details are paused: today's data allowance from the stats provider is used up. "
                   "They come back after midnight UTC (1 am in Lagos).")
                  if played and events is None and budget_used_up() else None,
    }


# ---------------------------------------------------------------- player search

PLAYER_DAILY = int(os.environ.get("API_FOOTBALL_PLAYER_LIMIT", "25"))  # requests a day that player search may use
_player_calls = {"day": None, "n": 0}
_search_cache = {}    # normalised query -> (fetched_at, [players])
_profile_cache = {}   # player id -> (fetched_at, profile)


def _player_budget():
    """Player pages must never eat the allowance that live scores and line-ups need."""
    today = datetime.now(timezone.utc).date()
    if _player_calls["day"] != today:
        _player_calls.update(day=today, n=0)
    if _player_calls["n"] >= PLAYER_DAILY:
        return False
    _player_calls["n"] += 1
    return True


def _player(p):
    p = p or {}
    return {"id": p.get("id"), "name": p.get("name") or "", "firstname": p.get("firstname"), "lastname": p.get("lastname"),
            "age": p.get("age"), "nationality": p.get("nationality"), "position": p.get("position"),
            "height": p.get("height"), "weight": p.get("weight"), "photo": p.get("photo"),
            "birth": (p.get("birth") or {}).get("date"), "birthplace": (p.get("birth") or {}).get("place")}


def search_players(q, key):
    """Players whose name matches q (3+ letters). Returns (list, error) where error is None or a short reason."""
    q = " ".join((q or "").split())[:40]
    norm = _norm(q)
    if len(re.sub(r"[^a-z]", "", norm)) < 3:
        return [], "short"
    hit = _search_cache.get(norm)
    if hit and time.time() - hit[0] < 86400:
        return hit[1], None
    if not _player_budget():
        return [], "busy"
    data = _get("/players/profiles", key, search=re.sub(r"[^a-z ]", "", norm).strip())
    if data is None:
        return [], "unavailable"
    players = [_player((r or {}).get("player")) for r in (data.get("response") or [])]
    players = [p for p in players if p["id"] and p["name"]][:20]
    if len(_search_cache) > 500:
        _search_cache.clear()
    _search_cache[norm] = (time.time(), players)
    return players, None


def player_profile(pid, key):
    """One player's details plus the clubs and national teams they've been listed with, or (None, reason)."""
    hit = _profile_cache.get(pid)
    if hit and time.time() - hit[0] < 7 * 86400:
        return hit[1], None
    if not _player_budget():
        return None, "busy"
    data = _get("/players/profiles", key, player=pid)
    rows = (data or {}).get("response") or []
    if not rows:
        return None, "unavailable" if data is None else "missing"
    profile = _player(rows[0].get("player"))
    profile["teams"] = []
    if _player_budget():  # best effort: the profile is still useful without the club list
        teams = _get("/players/teams", key, player=pid)
        found = []
        for r in (teams or {}).get("response") or []:
            t, seasons = (r or {}).get("team") or {}, [s for s in (r or {}).get("seasons") or [] if isinstance(s, int)]
            if t.get("name") and seasons:
                found.append({"name": t["name"], "logo": t.get("logo"), "last": max(seasons), "first": min(seasons)})
        profile["teams"] = sorted(found, key=lambda t: (-t["last"], t["name"]))[:8]
    if len(_profile_cache) > 500:
        _profile_cache.clear()
    _profile_cache[pid] = (time.time(), profile)
    return profile, None


# ---------------------------------------------------------------- who scored (for notifications)

def latest_goal(m, key, side, nth):
    """The scorer of a team's nth goal in a match: {"player", "minute", "kind", "assist"}, or None if not available yet.

    One request (plus one more the first time the match has to be found). Goals the provider hasn't recorded yet give None,
    so the caller can try again a few seconds later. An own goal counts for the team that benefits.
    """
    if budget_used_up() or nth < 1:
        return None
    fx = _find_fixture(m, key)
    if not fx:
        return None
    home_id = fx["teams"]["home"]["id"]
    data = _get("/fixtures/events", key, fixture=fx["fixture"]["id"])
    found = []
    for e in (data or {}).get("response") or []:
        if e.get("type") != "Goal" or e.get("detail") == "Missed Penalty":
            continue
        own = e.get("detail") == "Own Goal"
        by_home = ((e.get("team") or {}).get("id") == home_id) != own
        if by_home != (side == "home"):
            continue
        t = e.get("time") or {}
        found.append(((t.get("elapsed") or 0) * 100 + (t.get("extra") or 0), e, t))
    found.sort(key=lambda x: x[0])
    if len(found) < nth:
        return None
    _order, e, t = found[nth - 1]
    minute = ""
    if t.get("elapsed") is not None:
        minute = f"{t['elapsed']}+{t['extra']}'" if t.get("extra") else f"{t['elapsed']}'"
    return {"player": (e.get("player") or {}).get("name") or "", "minute": minute,
            "kind": {"Penalty": "pen", "Own Goal": "og"}.get(e.get("detail"), ""),
            "assist": (e.get("assist") or {}).get("name") or ""}