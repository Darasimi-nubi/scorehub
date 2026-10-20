"""Real-time football scores from ESPN's public scoreboard feed, so the 100-a-day API-Football allowance is kept for
things only it can do (line-ups, stats, player ratings).

How it fits in: for a football match that should be in play, we look it up in ESPN's feed for its competition. Found:
status, score and minute come from ESPN and API-Football is not asked. Not found, or ESPN failing: nothing changes and the
old path (API-Football, then the time-based estimate) takes over. So if ESPN changes or disappears, the site keeps working.

It's an unofficial feed with no published limits and no guarantees, so it is polled gently (one request per competition,
shared by every visitor, at most every ESPN_LIVE_TTL seconds). Set ESPN_LIVE=0 in Railway to switch it off.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import requests

import apifootball  # only for the team-name matching helpers

ENABLED = os.environ.get("ESPN_LIVE", "1") != "0"
LIVE_TTL = int(os.environ.get("ESPN_LIVE_TTL", "25"))   # seconds between refreshes of a competition's live board
DAY_TTL = 600                                            # finished/past boards change rarely
URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"

# our competition name (as stored from football-data.org) -> ESPN's feed name
SLUGS = {
    "Premier League": "eng.1", "Championship": "eng.2", "Primera Division": "esp.1", "La Liga": "esp.1",
    "Serie A": "ita.1", "Bundesliga": "ger.1", "Ligue 1": "fra.1", "Eredivisie": "ned.1", "Primeira Liga": "por.1",
    "Campeonato Brasileiro Série A": "bra.1", "UEFA Champions League": "uefa.champions", "Champions League": "uefa.champions",
    "Europa League": "uefa.europa", "UEFA Europa League": "uefa.europa", "Copa Libertadores": "conmebol.libertadores",
}

_boards = {}   # (slug, "YYYYMMDD") -> (fetched_at, [events])
_dead = set()  # slugs ESPN doesn't have: reported once, then skipped
_failing = {}  # slug -> time of last failure, so a broken feed isn't hammered


def _fetch(slug, day, ttl):
    """Events on one competition's board for one UTC day, or None if unavailable. Cached and shared."""
    if not ENABLED or slug in _dead:
        return None
    key = (slug, day)
    hit = _boards.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if time.time() - _failing.get(slug, 0) < 60:
        return hit[1] if hit and time.time() - hit[0] < ttl * 4 else None
    try:
        resp = requests.get(URL.format(slug=slug), params={"dates": day}, headers={"User-Agent": "Mozilla/5.0 ScoreHub"}, timeout=10)
        if resp.status_code == 404:
            print(f"espn-live: ESPN has no feed called '{slug}'; skipping it")
            _dead.add(slug)
            return None
        resp.raise_for_status()
        events = resp.json().get("events") or []
    except Exception as exc:
        _failing[slug] = time.time()
        print(f"espn-live: {slug} {day} failed: {exc}")
        return hit[1] if hit and time.time() - hit[0] < ttl * 4 else None
    if len(_boards) > 200:
        _boards.clear()
    _boards[key] = (time.time(), events)
    return events


def _teams(event):
    comp = (event.get("competitions") or [{}])[0]
    by = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    return comp, by.get("home"), by.get("away")


def _name(c):
    t = (c or {}).get("team") or {}
    return t.get("displayName") or t.get("shortDisplayName") or t.get("name") or ""


def _alt_name(c):
    t = (c or {}).get("team") or {}
    return t.get("shortDisplayName") or t.get("name") or ""


def _sim(ours, c):
    return max(apifootball._similar(ours, _name(c)), apifootball._similar(ours, _alt_name(c)))


def _find(row, events):
    """The ESPN event for one of our rows: kick-off within 90 minutes, and both team names alike."""
    kick = datetime.fromisoformat(row["kickoff"]).timestamp()
    best, best_score = None, 0.0
    for ev in events:
        try:
            when = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if abs(when - kick) > 90 * 60:
            continue
        comp, home, away = _teams(ev)
        if not home or not away:
            continue
        score = (_sim(row["home"], home) + _sim(row["away"], away)) / 2
        if score > best_score:
            best, best_score = ev, score
    return best if best_score >= 0.7 else None


def _score(c):
    try:
        return int(c.get("score"))
    except (TypeError, ValueError):
        return None


def _apply(row, ev):
    """Copy ESPN's status, score and minute onto our row. Returns True when ESPN has a usable answer."""
    comp, home, away = _teams(ev)
    st = ev.get("status") or {}
    kind = st.get("type") or {}
    name, state = kind.get("name", ""), kind.get("state", "")
    if name in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED"):
        row["status"] = "postponed"
        return True
    if state == "in":
        row["status"] = "live"
        row["home_score"], row["away_score"] = _score(home), _score(away)
        n = st.get("period") or 1
        if name == "STATUS_HALFTIME":
            row["period"], row["clock"] = "HT", None
        else:
            row["period"] = {1: "1H", 2: "2H", 3: "ET", 4: "ET", 5: "PEN"}.get(n, "2H")
            clock = (st.get("displayClock") or "").strip()
            row["clock"] = clock or None
        return True
    if state == "post":
        row["status"] = "finished"
        row["period"], row["clock"] = "FT", None
        row["home_score"], row["away_score"] = _score(home), _score(away)
        return True
    if state == "pre":
        return True  # ESPN knows the match and says it hasn't started: leave our row alone
    return False


def _maybe_live(row, now):
    if row["status"] not in ("scheduled", "live"):
        return False
    since = (now - datetime.fromisoformat(row["kickoff"])).total_seconds() / 60
    return -2 <= since <= 150


def _maybe_stale(row, now):
    """Listed as 'scheduled' by our delayed feed although it kicked off more than ~2 hours ago (up to two days)."""
    if row["status"] != "scheduled":
        return False
    since = (now - datetime.fromisoformat(row["kickoff"])).total_seconds() / 60
    return 130 <= since <= 48 * 60


def overlay(rows):
    """Update football rows in place from ESPN where possible. Returns the ids of rows ESPN answered for."""
    if not ENABLED:
        return set()
    now = datetime.now(timezone.utc)
    handled = set()
    for r in rows:
        slug = SLUGS.get(r.get("league"))
        if not slug:
            continue
        live, stale = _maybe_live(r, now), _maybe_stale(r, now)
        if not (live or stale):
            continue
        kick = datetime.fromisoformat(r["kickoff"]).astimezone(timezone.utc)
        # a match late in the evening UTC can sit on the next day's board in some feeds: look at both days around kickoff
        days = [kick.strftime("%Y%m%d")]
        if live and (now.date() != kick.date()):
            days.append(now.strftime("%Y%m%d"))
        for day in dict.fromkeys(days):
            events = _fetch(slug, day, LIVE_TTL if live else DAY_TTL)
            if events is None:
                continue
            ev = _find(r, events)
            if ev and _apply(r, ev):
                handled.add(r["id"])
                break
    return handled


def latest_goal(row, side, nth):
    """Who scored a team's nth goal, from ESPN's scoring plays: {"player", "minute", "kind", "assist"} or None.

    ESPN's scoreboard lists scoring plays under the match's "details". The shape isn't documented, so everything is read
    defensively and anything unexpected simply gives None (the caller then asks API-Football instead).
    """
    slug = SLUGS.get(row.get("league"))
    if not slug or nth < 1:
        return None
    kick = datetime.fromisoformat(row["kickoff"]).astimezone(timezone.utc)
    events = _fetch(slug, kick.strftime("%Y%m%d"), 10)
    ev = _find(row, events or [])
    if not ev:
        return None
    comp, home, away = _teams(ev)
    ids = {"home": str((home.get("team") or {}).get("id", "")), "away": str((away.get("team") or {}).get("id", ""))}
    found = []
    for d in comp.get("details") or []:
        if not d.get("scoringPlay"):
            continue
        own = bool(d.get("ownGoal"))
        team_id = str((d.get("team") or {}).get("id", ""))
        # an own goal is listed under the team of the player who scored it; it counts for the other side
        side_of = "home" if team_id == ids["home"] else "away" if team_id == ids["away"] else None
        if side_of is None:
            continue
        if own:
            side_of = "away" if side_of == "home" else "home"
        if side_of != side:
            continue
        who = (d.get("athletesInvolved") or [{}])[0]
        found.append({"player": who.get("displayName") or who.get("shortName") or "", "minute": (d.get("clock") or {}).get("displayValue") or "",
                      "kind": "og" if own else "pen" if d.get("penaltyKick") else "", "assist": ""})
    if len(found) < nth or not found[nth - 1]["player"]:
        return None
    return found[nth - 1]