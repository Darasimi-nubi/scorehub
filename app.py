import os
import sqlite3
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, abort, g, jsonify, render_template, request, send_from_directory

from config import load_env

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "scorehub.db")

load_env()  # reads FOOTBALL_DATA_KEY from the .env file, if there is one

# Used for league tables (and goal scorers on paid plans).
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY")
# Used for goal scorers and line-ups on the match page (api-sports.io, free plan).
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

import push  # goal notifications (after load_env, so it sees the VAPID keys)

app = Flask(__name__)

_detail_cache = {}  # external_id -> (fetched_at, detail dict or None)
DETAIL_TTL = 60     # seconds; keeps API usage low however many people watch a match

SPORTS = ("football", "basketball", "hockey")

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
        # Older databases only allowed football and basketball: rebuild the table without that limit.
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='leagues'").fetchone()
        if row and "CHECK (sport IN" in row[0]:
            conn.executescript("""
                PRAGMA foreign_keys = OFF;
                CREATE TABLE leagues_new (
                    id INTEGER PRIMARY KEY, sport TEXT NOT NULL, name TEXT NOT NULL, country TEXT,
                    UNIQUE (sport, name));
                INSERT INTO leagues_new SELECT id, sport, name, country FROM leagues;
                DROP TABLE leagues;
                ALTER TABLE leagues_new RENAME TO leagues;
                PRAGMA foreign_keys = ON;
            """)


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
        if sport in SPORTS:
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
        apifootball.settle_past(football, API_FOOTBALL_KEY)
    now = datetime.now(timezone.utc)
    for r in rows:
        if r["sport"] != "football" and r["status"] == "live" and \
                (now - datetime.fromisoformat(r["kickoff"])).total_seconds() > 4.5 * 3600:
            r["status"], r["period"], r["clock"] = "finished", "FT", None  # stale "live"
    for r in football:
        if r["status"] == "live" and (now - datetime.fromisoformat(r["kickoff"])).total_seconds() > 150 * 60:
            r["status"], r["period"], r["clock"] = "finished", "FT", None  # stale "live" from delayed data
        if r["status"] == "scheduled" and (now - datetime.fromisoformat(r["kickoff"])).total_seconds() > 240 * 60:
            r["status"], r["period"] = "finished", "FT"  # long over; the feed just hasn't caught up
        if r["status"] == "live" and not r.get("clock") and r.get("period") != "HT":
            r["period"], r["clock"] = estimate_clock(r["kickoff"], now)
    return rows


def live_rows(sport, league=None):
    """Matches in play right now, including ones our delayed data still lists as 'scheduled'."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sql = MATCH_SQL + """ WHERE (m.status = 'live'
                           OR (m.status = 'scheduled' AND m.kickoff >= ? AND m.kickoff <= ?))"""
    params = [(now - timedelta(minutes=140)).isoformat(), (now + timedelta(minutes=2)).isoformat()]
    if sport in SPORTS:
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


_h2h_cache = {}  # external_id -> (fetched_at, h2h dict or None)


def fd_h2h(external_id):
    """Past meetings from football-data.org (a second source for the H2H tab)."""
    if not FOOTBALL_DATA_KEY or not external_id.startswith("fd-"):
        return None
    hit = _h2h_cache.get(external_id)
    if hit and time.time() - hit[0] < (12 * 3600 if hit[1] else 1800):
        return hit[1]
    result = None
    try:
        resp = requests.get(f"https://api.football-data.org/v4/matches/{external_id[3:]}/head2head",
                            headers={"X-Auth-Token": FOOTBALL_DATA_KEY}, params={"limit": 10}, timeout=15)
        if resp.ok:
            data = resp.json()
            agg = data.get("aggregates") or {}
            home, away = agg.get("homeTeam") or {}, agg.get("awayTeam") or {}
            meetings, tally = [], {"home": 0, "draw": 0, "away": 0}
            for g in sorted(data.get("matches") or [], key=lambda g: g["utcDate"], reverse=True):
                ft = (g.get("score") or {}).get("fullTime") or {}
                if str(g.get("id")) == external_id[3:] or g.get("status") != "FINISHED" or ft.get("home") is None:
                    continue  # skip the match being viewed and anything unfinished
                win = (g.get("score") or {}).get("winner")
                winner_id = g["homeTeam"]["id"] if win == "HOME_TEAM" else g["awayTeam"]["id"] if win == "AWAY_TEAM" else None
                tally["draw" if winner_id is None else "home" if winner_id == home.get("id") else "away"] += 1
                meetings.append({
                    "date": g["utcDate"][:10], "league": (g.get("competition") or {}).get("name", ""),
                    "home": g["homeTeam"].get("shortName") or g["homeTeam"]["name"],
                    "away": g["awayTeam"].get("shortName") or g["awayTeam"]["name"],
                    "hs": ft["home"], "as": ft["away"],
                    "home_won": win == "HOME_TEAM", "away_won": win == "AWAY_TEAM"})
            if meetings:
                result = {"tally": tally, "meetings": meetings[:10]}
        else:
            print(f"football-data h2h: {resp.status_code} {resp.text[:120]}")
    except Exception as exc:
        print(f"football-data h2h failed: {exc}")
    _h2h_cache[external_id] = (time.time(), result)
    return result


def fetch_detail(m):
    """Goals, line-ups, venue and referee for a match row; API-Football if a key is set."""
    if API_FOOTBALL_KEY and m["sport"] == "football":
        import apifootball
        detail = apifootball.fetch_detail(m, API_FOOTBALL_KEY)
        if m["status"] != "postponed" and not (detail and detail.get("h2h")):
            h2h = fd_h2h(m["external_id"])
            if h2h:
                detail = detail or apifootball._notice(None)
                detail["h2h"] = h2h
        return detail
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
    if sport in SPORTS:
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


@app.route("/sw.js")
def service_worker():
    """Must be served from the site root so it can receive pushes for every page."""
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/push/key")
def push_key():
    if not push.enabled():
        abort(404)
    return jsonify(key=push.VAPID_PUBLIC)


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    if not push.enabled():
        abort(404)
    body = request.get_json(silent=True) or {}
    sub = body.get("subscription") or {}
    keys = sub.get("keys") or {}
    if not push.valid_endpoint(sub.get("endpoint", "")) or not keys.get("p256dh") or not keys.get("auth"):
        abort(400)
    matches = body.get("matches") if isinstance(body.get("matches"), list) else []
    push.save({"endpoint": sub["endpoint"], "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}, matches)
    return jsonify(ok=True)


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    endpoint = (request.get_json(silent=True) or {}).get("endpoint", "")
    if endpoint:
        push.remove(endpoint)
    return jsonify(ok=True)


def _live_for_push():
    with app.app_context():
        return live_rows("football")


def start_push():
    """Background goal watcher (one per process; the site runs a single worker)."""
    if not push.enabled():
        print("push: VAPID keys not set, goal notifications are off")
        return
    import threading
    threading.Thread(target=push.run, args=(_live_for_push,), daemon=True).start()


init_db()  # also runs under gunicorn, so the tables always exist


def start_sync():
    """Run the live-score sync in a background thread (used online, where there's one process)."""
    import threading
    import sync
    if not FOOTBALL_DATA_KEY:
        return
    sync.clear_demo_data()
    start_push()
    threading.Thread(
        target=sync.run_football, args=(sync.FootballDataProvider(FOOTBALL_DATA_KEY),),
        kwargs={"extras": (sync.EspnBasketballProvider(), sync.EspnHockeyProvider())}, daemon=True
    ).start()


if os.environ.get("RUN_SYNC") == "1":
    start_sync()


if __name__ == "__main__":
    if not FOOTBALL_DATA_KEY:
        print("Note: FOOTBALL_DATA_KEY not found (.env file or terminal). League tables will be unavailable.")
    app.run(debug=True)
