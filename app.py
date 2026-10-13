import os
import sqlite3
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, abort, g, render_template, request

from config import load_env

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "scorehub.db")

load_env()  # reads FOOTBALL_DATA_KEY from the .env file, if there is one

# Used for league tables (and goal scorers on paid plans).
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY")
# Used for goal scorers and line-ups on the match page (api-sports.io, free plan).
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

app = Flask(__name__)

_detail_cache = {}  # external_id -> (fetched_at, detail dict or None)
DETAIL_TTL = 60     # seconds; keeps API usage low however many people watch a match

MATCH_SQL = """
    SELECT m.*, l.name AS league, l.country, l.sport,
           h.name AS home, a.name AS away,
           h.crest AS home_crest, a.crest AS away_crest
    FROM matches m
    JOIN leagues l ON l.id = m.league_id
    JOIN teams h ON h.id = m.home_id
    JOIN teams a ON a.id = m.away_id
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        with open(os.path.join(BASE_DIR, "schema.sql")) as f:
            conn.executescript(f.read())


def viewer_offset():
    """The viewer's UTC offset in minutes (Lagos = 60), sent by the page in a cookie."""
    try:
        return max(-840, min(840, int(request.cookies.get("tz", "0"))))
    except ValueError:
        return 0


def local_today(offset):
    return (datetime.now(timezone.utc) + timedelta(minutes=offset)).date()


def load_groups(sport, day, league=None, offset=0, live=False):
    """Matches for one calendar day in the viewer's time zone (or everything live right now), grouped by league."""
    # Local midnight expressed in UTC: the day starts `offset` minutes earlier than UTC midnight.
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc) - timedelta(minutes=offset)
    end = start + timedelta(days=1)
    if live:
        rows = live_rows(sport, league)
    else:
        sql = MATCH_SQL + " WHERE m.kickoff >= ? AND m.kickoff < ?"
        params = [start.isoformat(), end.isoformat()]
        if sport in ("football", "basketball"):
            sql += " AND l.sport = ?"
            params.append(sport)
        if league:
            sql += " AND l.name = ?"
            params.append(league)
        sql += " ORDER BY l.sport, l.name, m.kickoff"
        rows = decorate(get_db().execute(sql, params).fetchall())

    groups = OrderedDict()
    for row in rows:
        groups.setdefault((row["sport"], row["league"], row["country"]), []).append(row)
    return groups


def estimate_clock(kickoff, now):
    """Match minute worked out from kickoff time, for when no live feed is available."""
    mins = int((now - datetime.fromisoformat(kickoff)).total_seconds() // 60)
    if mins < 0:
        return None, None
    if mins <= 45:
        return "1H", f"{max(mins, 1)}'"
    if mins <= 47:
        return "1H", "45+'"
    if mins <= 62:
        return "HT", None  # 15-minute break
    minute = mins - 15
    return "2H", f"{minute}'" if minute <= 90 else "90+'"


def decorate(rows):
    """Turn rows into dicts and fix up live matches.

    Real-time status, score and minute come from API-Football when a key is set; otherwise
    (or when its budget is used up) the minute is estimated from the kickoff time.
    """
    rows = [dict(r) for r in rows]
    football = [r for r in rows if r["sport"] == "football"]
    if API_FOOTBALL_KEY and football:
        import apifootball
        apifootball.overlay_live(football, API_FOOTBALL_KEY)
    now = datetime.now(timezone.utc)
    for r in football:
        if r["status"] == "live" and not r.get("clock") and r.get("period") != "HT":
            r["period"], r["clock"] = estimate_clock(r["kickoff"], now)
    return rows


def live_rows(sport, league=None):
    """Matches in play right now, including ones our delayed data still lists as 'scheduled'."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sql = MATCH_SQL + """ WHERE (m.status = 'live'
                           OR (m.status = 'scheduled' AND m.kickoff >= ? AND m.kickoff <= ?))"""
    params = [(now - timedelta(minutes=140)).isoformat(), (now + timedelta(minutes=2)).isoformat()]
    if sport in ("football", "basketball"):
        sql += " AND l.sport = ?"
        params.append(sport)
    if league:
        sql += " AND l.name = ?"
        params.append(league)
    sql += " ORDER BY l.sport, l.name, m.kickoff"
    rows = decorate(get_db().execute(sql, params).fetchall())
    return [r for r in rows if r["status"] == "live"]


def get_match(match_id):
    row = get_db().execute(MATCH_SQL + " WHERE m.id = ?", (match_id,)).fetchone()
    return decorate([row])[0] if row else None


def _lineup(team):
    """Starting XI, bench, formation and coach for one side, from football-data.org's match data."""
    team = team or {}

    def players(items):
        return [{"name": p.get("name") or "Unknown", "number": p.get("shirtNumber"),
                 "position": p.get("position")} for p in items or []]

    return {
        "formation": team.get("formation"),
        "coach": (team.get("coach") or {}).get("name"),
        "starters": players(team.get("lineup")),
        "bench": players(team.get("bench")),
    }


def fetch_detail(m):
    """Goals, line-ups, venue and referee for a match row; API-Football if a key is set."""
    if API_FOOTBALL_KEY and m["sport"] == "football":
        import apifootball
        return apifootball.fetch_detail(m, API_FOOTBALL_KEY)
    return _fetch_detail_fd(m["external_id"])


def _fetch_detail_fd(external_id):
    """Goal scorers and venue from football-data.org for one match, cached briefly.

    Returns None when unavailable (no key, demo match, API error, or a plan that
    doesn't include the data). The page then simply hides that section.
    """
    if not FOOTBALL_DATA_KEY or not (external_id or "").startswith("fd-"):
        return None
    cached = _detail_cache.get(external_id)
    if cached and time.time() - cached[0] < DETAIL_TTL:
        return cached[1]

    detail = cached[1] if cached else None  # keep showing old data if the call fails
    try:
        resp = requests.get(
            f"https://api.football-data.org/v4/matches/{external_id[3:]}",
            headers={"X-Auth-Token": FOOTBALL_DATA_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        home_id = (data.get("homeTeam") or {}).get("id")
        goals = []
        for goal in data.get("goals") or []:
            minute = goal.get("minute")
            extra = goal.get("injuryTime")
            label = f"{minute}'" if minute is not None else ""
            if label and extra:
                label = f"{minute}+{extra}'"
            kind = {"PENALTY": "pen", "OWN": "og"}.get(goal.get("type"), "")
            goals.append({
                "minute": label,
                "player": (goal.get("scorer") or {}).get("name") or "Unknown",
                "kind": kind,
                "side": "home" if (goal.get("team") or {}).get("id") == home_id else "away",
            })
        lineups = {"home": _lineup(data.get("homeTeam")), "away": _lineup(data.get("awayTeam"))}
        has_lineups = lineups["home"]["starters"] or lineups["away"]["starters"]
        detail = {
            "lineups": lineups if has_lineups else None,
            "goals": goals,
            "venue": data.get("venue"),
            "referee": next(
                (r.get("name") for r in data.get("referees") or [] if r.get("type") == "REFEREE"),
                None,
            ),
        }
    except Exception as exc:
        print(f"match detail: request for {external_id} failed: {exc}")
    _detail_cache[external_id] = (time.time(), detail)
    return detail


# football-data.org competition codes, used for league tables.
LEAGUE_CODES = {
    "Premier League": "PL",
    "Primera Division": "PD", "La Liga": "PD",
    "Serie A": "SA",
    "Bundesliga": "BL1",
    "Ligue 1": "FL1",
    "UEFA Champions League": "CL", "Champions League": "CL",
    "Eredivisie": "DED",
    "Primeira Liga": "PPL",
    "Championship": "ELC",
    "Campeonato Brasileiro Série A": "BSA",
}
VALID_CODES = set(LEAGUE_CODES.values())
TABLE_TTL = 600  # seconds; tables change slowly, so 10 minutes keeps API usage tiny
_table_cache = {}


def code_for(league_name):
    return LEAGUE_CODES.get(league_name)


app.jinja_env.globals["table_code"] = code_for


def fetch_standings(code):
    """League table from football-data.org, cached. None if unavailable."""
    if not FOOTBALL_DATA_KEY:
        print("table: FOOTBALL_DATA_KEY is not set in this terminal, so no table can be fetched")
        return None
    cached = _table_cache.get(code)
    if cached and time.time() - cached[0] < TABLE_TTL:
        return cached[1]

    table = cached[1] if cached else None  # keep the old table if the call fails
    try:
        resp = requests.get(
            f"https://api.football-data.org/v4/competitions/{code}/standings",
            headers={"X-Auth-Token": FOOTBALL_DATA_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        groups = []
        for block in data.get("standings", []):
            if block.get("type") != "TOTAL":
                continue
            group = block.get("group")
            label = group.replace("_", " ").title() if group else None
            rows = []
            for r in block.get("table", []):
                team = r.get("team") or {}
                rows.append({
                    "position": r.get("position"),
                    "team": team.get("shortName") or team.get("name"),
                    "crest": team.get("crest"),
                    "played": r.get("playedGames"),
                    "won": r.get("won"), "draw": r.get("draw"), "lost": r.get("lost"),
                    "gf": r.get("goalsFor"), "ga": r.get("goalsAgainst"),
                    "gd": r.get("goalDifference"), "points": r.get("points"),
                })
            groups.append({"label": label, "rows": rows})
        season = data.get("season") or {}
        start, end = (season.get("startDate") or "")[:4], (season.get("endDate") or "")[2:4]
        table = {
            "name": (data.get("competition") or {}).get("name"),
            "season": f"{start}/{end}" if start and end else None,
            "matchday": season.get("currentMatchday"),
            "groups": groups,
        }
    except Exception as exc:
        detail_text = getattr(getattr(exc, "response", None), "text", "")[:200]
        print(f"table: request for {code} failed: {exc} {detail_text}")
    _table_cache[code] = (time.time(), table)
    return table


# Big competitions first in the filter bar; anything else follows alphabetically.
LEAGUE_ORDER = [
    "Premier League", "Primera Division", "La Liga", "Serie A", "Bundesliga", "Ligue 1",
    "UEFA Champions League", "Champions League", "Europa League", "Championship",
    "Eredivisie", "Primeira Liga",
]


def league_chips(sport):
    """League names that have matches in the database, in display order."""
    sql = """
        SELECT DISTINCT l.name, l.country FROM leagues l
        JOIN matches m ON m.league_id = l.id
    """
    params = []
    if sport in ("football", "basketball"):
        sql += " WHERE l.sport = ?"
        params.append(sport)
    leagues = [{"name": r["name"], "country": r["country"]} for r in get_db().execute(sql, params)]
    rank = {name: i for i, name in enumerate(LEAGUE_ORDER)}
    return sorted(leagues, key=lambda l: (rank.get(l["name"], len(LEAGUE_ORDER)), l["name"]))


def parse_args():
    sport = request.args.get("sport", "all")
    league = request.args.get("league") or None
    live = request.args.get("live") == "1"
    offset = viewer_offset()
    day = request.args.get("date") or local_today(offset).isoformat()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        day = local_today(offset).isoformat()
    return sport, day, league, live, offset


def day_nav(day, offset=0, span=7):
    """Previous/next day links, a friendly label, and the allowed date range (today -7 .. +7)."""
    today = local_today(offset)
    cur = datetime.strptime(day, "%Y-%m-%d").date()
    lo, hi = today - timedelta(days=span), today + timedelta(days=span)
    delta = (cur - today).days
    label = {0: "Today", -1: "Yesterday", 1: "Tomorrow"}.get(delta) or f"{cur:%a}, {cur.day} {cur:%b}"
    return {
        "label": label,
        "prev": (cur - timedelta(days=1)).isoformat() if cur > lo else None,
        "next": (cur + timedelta(days=1)).isoformat() if cur < hi else None,
        "min": lo.isoformat(), "max": hi.isoformat(),
    }


def live_count(sport):
    return len(live_rows(sport))


def badge_hue(name):
    """A stable colour per competition, for its round badge."""
    return sum(ord(c) * (i + 1) for i, c in enumerate(name)) % 360


def initials(name):
    words = [w for w in name.replace("-", " ").split() if w[0].isalnum()]
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(w[0] for w in words[:3]).upper()


app.jinja_env.globals.update(badge_hue=badge_hue, initials=initials)


@app.route("/")
def index():
    sport, day, league, live, offset = parse_args()
    return render_template("index.html", groups=load_groups(sport, day, league, offset, live),
                           sport=sport, day=day, league=league, live=live,
                           live_count=live_count(sport), nav=day_nav(day, offset),
                           leagues=league_chips(sport))


@app.route("/partial/matches")
def partial_matches():
    """HTML fragment the page polls to refresh live scores."""
    sport, day, league, live, offset = parse_args()
    return render_template("_matches.html", groups=load_groups(sport, day, league, offset, live),
                           live_count=live_count(sport))


@app.route("/table/<code>")
def table_page(code):
    code = code.upper()
    if code not in VALID_CODES:
        abort(404)
    return render_template("table.html", table=fetch_standings(code), code=code)


def match_or_404(match_id):
    m = get_match(match_id)
    if m is None:
        abort(404)
    return m


@app.route("/match/<int:match_id>")
def match_page(match_id):
    m = match_or_404(match_id)
    # "All matches" should return to the day the viewer sees this game on (their local day).
    kickoff = datetime.fromisoformat(m["kickoff"])
    back_date = (kickoff + timedelta(minutes=viewer_offset())).date().isoformat()
    return render_template("match.html", m=m, detail=fetch_detail(m),
                           back_date=back_date)


@app.route("/partial/match/<int:match_id>")
def partial_match(match_id):
    """HTML fragment the match page polls to refresh the score and goals."""
    m = match_or_404(match_id)
    return render_template("_match_body.html", m=m, detail=fetch_detail(m))


if __name__ == "__main__":
    init_db()
    if not FOOTBALL_DATA_KEY:
        print("Note: FOOTBALL_DATA_KEY not found (.env file or terminal). League tables will be unavailable.")
    app.run(debug=True)