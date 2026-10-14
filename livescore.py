"""LiveScore API (livescore-api.com) as the football data source.

Set LIVESCORE_KEY and LIVESCORE_SECRET. It supplies live scores and minutes, goals / cards /
substitutions, line-ups, match statistics and head-to-head. Its line-ups have no pitch positions,
ratings or photos, so the pitch view and ratings only appear when API-Football is also set up.

Every call is logged with the prefix "livescore:" so problems are easy to find in the Railway logs.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import requests

import apifootball as af  # shared name matching and per-part cache

KEY = os.environ.get("LIVESCORE_KEY", "").strip()
SECRET = os.environ.get("LIVESCORE_SECRET", "").strip()
BASE = "https://livescore-api.com/api-client/"
LIVE_TTL = int(os.environ.get("LIVESCORE_LIVE_TTL", "30"))        # seconds between live-score refreshes
DAILY_LIMIT = int(os.environ.get("LIVESCORE_DAILY_LIMIT", "0"))   # 0 = no cap

_calls = {"day": None, "n": 0}
_live = {"at": 0.0, "ok_at": 0.0, "rows": []}
_day_cache = {}   # "YYYY-MM-DD" -> (fetched_at, [matches])
_id_cache = {}    # our external_id -> LiveScore match dict


def enabled():
    return bool(KEY and SECRET)


def _roll():
    today = datetime.now(timezone.utc).date()
    if _calls["day"] != today:
        _calls.update(day=today, n=0)


def budget_used_up():
    _roll()
    return bool(DAILY_LIMIT) and _calls["n"] >= DAILY_LIMIT


def _get(target, **params):
    """GET a path (or a full URL taken from a match's `urls`), with retries. Returns `data` or None."""
    _roll()
    if budget_used_up():
        print("livescore: daily request budget used up")
        return None
    url = target if target.startswith("http") else BASE + target
    params = {k: v for k, v in params.items() if v is not None}
    params.setdefault("key", KEY)
    params.setdefault("secret", SECRET)
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers={"Connection": "close"}, timeout=15)
            _calls["n"] += 1
            body = resp.json()
            if not body.get("success", False):
                print(f"livescore: {url.split('/api-client/')[-1]} -> {str(body)[:200]}")
                return None
            return body.get("data")
        except Exception as exc:
            print(f"livescore: {url.split('/api-client/')[-1]} attempt {attempt + 1} failed: {exc}")
            time.sleep(1)
    return None


# ---------- finding the match ----------
def _score_pair(text):
    try:
        a, b = str(text).split("-")
        return int(a.strip()), int(b.strip())
    except (ValueError, AttributeError):
        return None, None


def _name_score(m, ls):
    return (af._similar(m["home"], (ls.get("home") or {}).get("name", "")) +
            af._similar(m["away"], (ls.get("away") or {}).get("name", ""))) / 2


def _best(m, candidates):
    scored = [(_name_score(m, c), c) for c in candidates]
    scored = [s for s in scored if s[0] >= 0.75]
    return max(scored, key=lambda s: s[0])[1] if scored else None


def live_matches():
    """Every match in play (and recently finished), one request shared by all visitors."""
    now = time.time()
    if now - _live["at"] >= LIVE_TTL:
        _live["at"] = now
        data = _get("matches/live.json")
        if data is not None:
            _live.update(ok_at=now, rows=data.get("match") or [])
    return _live["rows"], now - _live["ok_at"] < LIVE_TTL * 6


def _history(day):
    """Finished matches of one date (30 per page; a few pages cover the big leagues)."""
    hit = _day_cache.get(day)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    found = []
    for page in range(1, 9):
        data = _get("matches/history.json", **{"from": day, "to": day, "page": page})
        chunk = (data or {}).get("match") or []
        found += chunk
        if len(chunk) < 30:
            break
    _day_cache[day] = (time.time() if found else time.time() - 540, found)
    return found


def find_match(m):
    cached = _id_cache.get(m["external_id"])
    if cached:
        return cached
    live, _ = live_matches()
    hit = _best(m, live)
    if not hit and m["status"] != "scheduled":
        day = datetime.fromisoformat(m["kickoff"]).astimezone(timezone.utc).date().isoformat()
        hit = _best(m, _history(day))
    if hit:
        _id_cache[m["external_id"]] = hit
    return hit


# ---------- live scores ----------
def overlay_live(rows):
    """Update football rows (dicts) in place with real-time status, score and minute."""
    now = datetime.now(timezone.utc)
    candidates = [r for r in rows if af._maybe_live(r, now)]
    if not candidates:
        return rows
    live, fresh = live_matches()
    if not fresh:
        return rows
    for r in candidates:
        ls = _best(r, live)
        if not ls:
            since = (now - datetime.fromisoformat(r["kickoff"])).total_seconds() / 60
            if r["status"] == "live" and since >= 110:
                r["status"], r["period"], r["clock"] = "finished", "FT", None
            continue
        status = str(ls.get("status", "")).upper()
        hs, as_ = _score_pair((ls.get("scores") or {}).get("score"))
        if status == "FINISHED":
            r["status"], r["period"], r["clock"] = "finished", "FT", None
        elif status in ("IN PLAY", "ADDED TIME", "HALF TIME BREAK"):
            r["status"] = "live"
            minute = str(ls.get("time") or "").strip()
            if status == "HALF TIME BREAK" or minute.upper() == "HT":
                r["period"], r["clock"] = "HT", None
            else:
                digits = "".join(ch for ch in minute.split("+")[0] if ch.isdigit())
                r["period"] = "2H" if digits and int(digits) > 45 else "1H"
                r["clock"] = f"{minute}'" if minute and minute[0].isdigit() else None
        else:
            continue  # not started / unknown: leave our own data alone
        if hs is not None:
            r["home_score"], r["away_score"] = hs, as_
    return rows


def settle_past(rows):
    """Matches our delayed feed still lists as 'scheduled' although they finished long ago."""
    now = datetime.now(timezone.utc)
    for r in rows:
        if r["status"] != "scheduled":
            continue
        kick = datetime.fromisoformat(r["kickoff"])
        since = (now - kick).total_seconds() / 60
        if since < 130 or since > 48 * 60:
            continue
        ls = _best(r, _history(kick.astimezone(timezone.utc).date().isoformat()))
        if ls and str(ls.get("status", "")).upper() == "FINISHED":
            hs, as_ = _score_pair((ls.get("scores") or {}).get("score"))
            r["status"], r["period"], r["clock"] = "finished", "FT", None
            if hs is not None:
                r["home_score"], r["away_score"] = hs, as_
    return rows


# ---------- match details ----------
def _minute(value):
    text = str(value)
    if "+" in text:
        base, extra = text.split("+", 1)
        base, extra = int(base or 0), int(extra or 0)
        return f"{base}+{extra}'", base, base * 100 + extra
    base = int(text) if text.isdigit() else 0
    return f"{base}'", base, base * 100


def _lineups(data):
    if not data:
        return None
    root = data.get("lineup") if isinstance(data, dict) else None
    if not root:
        return None
    out = {}
    for side in ("home", "away"):
        team = root.get(side) or {}
        starters, bench = [], []
        for p in team.get("players") or []:
            num = str(p.get("shirt_number") or "")
            person = {"id": p.get("id"), "name": p.get("name") or "Unknown",
                      "number": int(num) if num.isdigit() else None, "position": None, "grid": None, "photo": None}
            (bench if str(p.get("substitution")) == "1" else starters).append(person)
        out[side] = {"formation": None, "coach": None, "starters": starters, "bench": bench}
    if not (out["home"]["starters"] or out["away"]["starters"]):
        return None
    out["pitch"] = False  # no pitch positions in this feed: show plain starting-eleven lists
    return out


def _events(data, lineups, status):
    """Goals, cards, subs -> (goals list, timeline rows, per-player marks, subs per side)."""
    people, starters, came_on = {}, set(), set()
    if lineups:
        for s in ("home", "away"):
            for p in lineups[s]["starters"] + lineups[s]["bench"]:
                people[str(p["id"])] = p
            starters |= {str(p["id"]) for p in lineups[s]["starters"]}
    marks, subs, goals, rows = {}, {"home": [], "away": []}, [], []
    score = [0, 0]

    def mark(pid):
        return marks.setdefault(str(pid), {"goals": 0, "og": 0, "yellow": 0, "red": 0, "off": None, "on": None})

    def person(p):
        known = people.get(str((p or {}).get("id"))) or {}
        return {"name": known.get("name") or (p or {}).get("name") or "Unknown", "number": known.get("number")}

    events = sorted((data or {}).get("event") or [], key=lambda e: _minute(e.get("time", 0))[2])
    for e in events:
        kind = e.get("event")
        label, elapsed, order = _minute(e.get("time", 0))
        side = "home" if e.get("is_home") else "away"
        p, info = e.get("player") or {}, e.get("info") or {}
        row = {"order": order, "elapsed": elapsed, "minute": label, "side": side, "kind": "",
               "player": p.get("name") or "", "other": "", "score": "", "note": ""}
        if kind in ("GOAL", "GOAL_PENALTY", "OWN_GOAL"):
            own = kind == "OWN_GOAL"
            scoring = ("away" if side == "home" else "home") if own else side
            score[0 if scoring == "home" else 1] += 1
            row.update(kind="goal", side=scoring, other=info.get("name") or "" if not own else "",
                       score=f"{score[0]} - {score[1]}", note="OG" if own else "PEN" if kind == "GOAL_PENALTY" else "")
            goals.append({"minute": label, "player": p.get("name") or "Unknown",
                          "kind": "og" if own else "pen" if kind == "GOAL_PENALTY" else "", "side": scoring})
            if p.get("id"):
                mark(p["id"])["og" if own else "goals"] += 1
        elif kind in ("YELLOW_CARD", "RED_CARD", "YELLOW_RED_CARD"):
            row["kind"] = "yellow" if kind == "YELLOW_CARD" else "red"
            if p.get("id"):
                mark(p["id"])["yellow" if kind == "YELLOW_CARD" else "red"] = 1
        elif kind == "MISSED_PENALTY":
            row.update(kind="pen-miss", note="Missed penalty")
        elif kind == "SUBSTITUTION":
            a, b = p, info  # whoever started (or already came on) is the one leaving
            if str(b.get("id")) in starters or str(b.get("id")) in came_on:
                a, b = b, a
            came_on.add(str(b.get("id")))
            row.update(kind="sub", player=person(b)["name"], other=person(a)["name"])
            if a.get("id"):
                mark(a["id"])["off"] = label
            if b.get("id"):
                mark(b["id"])["on"] = label
            subs[side].append({"minute": label, "order": order, "in": person(b), "out": person(a)})
        else:
            continue
        if row["player"]:
            rows.append(row)

    timeline, ht_done, shown = [], False, [0, 0]
    for r in rows:
        if not ht_done and r["elapsed"] > 45:
            ht_done = True
            timeline.append({"kind": "divider", "minute": "HT", "score": f"{shown[0]} - {shown[1]}"})
        if r["score"]:
            shown = [int(x) for x in r["score"].split(" - ")]
        timeline.append(r)
    if status == "finished":
        if not ht_done:
            timeline.append({"kind": "divider", "minute": "HT", "score": f"{shown[0]} - {shown[1]}"})
        timeline.append({"kind": "divider", "minute": "FT", "score": f"{score[0]} - {score[1]}"})
    return goals, timeline, marks, subs


STATS = [("possesion", "Possession"), ("shots_on_target", "Shots on target"), ("attempts_on_goal", "Shots"),
         ("corners", "Corners"), ("fauls", "Fouls"), ("offsides", "Offsides"), ("yellow_cards", "Yellow cards"),
         ("red_cards", "Red cards"), ("saves", "Saves")]


def _stats(data):
    by_type = {s.get("type"): s for s in (data or []) if isinstance(s, dict)}
    rows = []
    for key, label in STATS:
        s = by_type.get(key)
        if not s:
            continue
        h, a = af._number(s.get("home")), af._number(s.get("away"))
        if key == "possesion":  # percentages
            hv, av = f"{int(h)}%", f"{int(a)}%"
        else:
            hv, av = int(h) if h == int(h) else h, int(a) if a == int(a) else a
        total = h + a
        rows.append({"label": label, "home": hv, "away": av,
                     "home_pct": round(h / total * 100) if total else 50,
                     "away_pct": round(a / total * 100) if total else 50,
                     "lead": "home" if h > a else "away" if a > h else ""})
    return rows


def _h2h(data, home_id, away_id):
    done, tally = [], {"home": 0, "draw": 0, "away": 0}
    for g in (data or {}).get("h2h") or []:
        hs, as_ = _score_pair(g.get("ft_score") or g.get("score"))
        if hs is None:
            continue
        gh = str(g.get("home_id"))
        winner = None if hs == as_ else (gh if hs > as_ else str(g.get("away_id")))
        tally["draw" if winner is None else "home" if winner == str(home_id) else "away"] += 1
        done.append({"date": (g.get("date") or "")[:10], "league": (g.get("competition") or {}).get("name", ""),
                     "home": g.get("home_name", ""), "away": g.get("away_name", ""), "hs": hs, "as": as_,
                     "home_won": hs > as_, "away_won": as_ > hs})
    done.sort(key=lambda g: g["date"], reverse=True)
    return {"tally": tally, "meetings": done[:10]} if done else None


def fetch_detail(m):
    """Same shape as apifootball.fetch_detail, or None when this match can't be found."""
    if m["status"] == "postponed":
        return None
    kick = datetime.fromisoformat(m["kickoff"])
    now = datetime.now(timezone.utc)
    if kick - now > timedelta(hours=26):
        return None
    ls = find_match(m)
    if not ls:
        return None
    ext, status = m["external_id"], m["status"]
    played = status in ("live", "finished")
    soon = kick - now <= timedelta(hours=2)
    urls = ls.get("urls") or {}
    final = 6 * 3600
    mid = ls.get("id")

    def load_events():
        data = _get(urls["events"]) if urls.get("events") else _get("matches/events.json", id=mid)
        return af.FAIL if data is None else data

    def load_lineups():
        data = _get(urls["lineups"]) if urls.get("lineups") else _get("matches/lineups.json", match_id=mid)
        return af.FAIL if data is None else (_lineups(data) or None)

    def load_stats():
        data = _get(urls["statistics"]) if urls.get("statistics") else _get("matches/statistics.json", match_id=mid)
        return af.FAIL if data is None else _stats(data)

    def load_h2h():
        data = _get(urls["head2head"]) if urls.get("head2head") else _get(
            "teams/head2head.json", team1_id=(ls.get("home") or {}).get("id"), team2_id=(ls.get("away") or {}).get("id"))
        return af.FAIL if data is None else _h2h(data, (ls.get("home") or {}).get("id"), (ls.get("away") or {}).get("id"))

    ttl = lambda live_s: final if status == "finished" else live_s
    raw = af._cached("ls:" + ext, "events", ttl(90), load_events) if played else None
    stats = af._cached("ls:" + ext, "stats", ttl(240), load_stats) if played else []
    lineups = af._cached("ls:" + ext, "lineups", 6 * 3600, load_lineups) if (soon or played) else None
    if (soon or played) and not lineups:  # not published yet: look again in a few minutes
        af._detail_cache["ls:" + ext]["lineups"] = (time.time() - 6 * 3600 + 300, None)
    h2h = af._cached("ls:" + ext, "h2h", 12 * 3600, load_h2h)
    if not h2h:
        af._detail_cache["ls:" + ext]["h2h"] = (time.time() - 12 * 3600 + 1800, None)

    goals, timeline, marks, subs = _events(raw, lineups, status) if raw else ([], [], {}, {"home": [], "away": []})
    return {"goals": goals, "stats": stats or [], "lineups": lineups, "marks": marks, "subs": subs,
            "h2h": h2h, "timeline": timeline, "ratings": {}, "motm": None,
            "venue": ls.get("location"), "referee": None, "notice": None}