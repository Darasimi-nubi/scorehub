import gzip
import os
import sqlite3
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, send_from_directory, url_for

from config import load_env

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "scorehub.db")

load_env()  # reads FOOTBALL_DATA_KEY from the .env file, if there is one

# Used for league tables (and goal scorers on paid plans).
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY")
# Used for goal scorers and line-ups on the match page (api-sports.io, free plan).
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

# Google AdSense publisher id (looks like ca-pub-1234567890123456); leave unset to show no ads.
ADSENSE_CLIENT = (os.environ.get("ADSENSE_CLIENT") or "").strip()
# Public contact address shown on the Contact page (use an address you are happy to publish).
CONTACT_EMAIL = (os.environ.get("CONTACT_EMAIL") or "").strip()
SITE_URL = (os.environ.get("SITE_URL") or "https://scorehubber.name.ng").rstrip("/")

import card  # share-preview pictures
import matchlinks  # permanent match ids and readable links
import content  # written league guides and explainers
import push  # goal notifications (after load_env, so it sees the VAPID keys)
import highlights  # match highlight videos (YOUTUBE_API_KEY)

app = Flask(__name__)

_detail_cache = {}  # external_id -> (fetched_at, detail dict or None)
DETAIL_TTL = 60     # seconds; keeps API usage low however many people watch a match

SPORTS = ("football", "basketball", "hockey", "tennis", "cricket")

MATCH_SQL = """
    SELECT m.*, l.name AS league, l.country, l.sport,
           h.name AS home, a.name AS away,
           h.crest AS home_crest, a.crest AS away_crest
    FROM matches m
    JOIN leagues l ON l.id = m.league_id
    JOIN teams h ON h.id = m.home_id
    JOIN teams a ON a.id = m.away_id
"""


# ---- Speed: smaller downloads, cached static files, and a few seconds of memory for busy pages ----

app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000  # static files carry their file date in the link (see below), so they can be kept a year

COMPRESSIBLE = {"text/html", "text/css", "text/plain", "text/javascript", "application/javascript", "application/json",
                "application/xml", "application/manifest+json", "image/svg+xml"}


@app.url_defaults
def version_static(endpoint, values):
    """/static/style.css becomes /static/style.css?v=<file date>, so a changed file is fetched again but an unchanged one never is."""
    if endpoint == "static" and "v" not in values and values.get("filename"):
        try:
            values["v"] = int(os.path.getmtime(os.path.join(app.static_folder, values["filename"])))
        except OSError:
            pass


@app.after_request
def compress(resp):
    """gzip text responses for browsers that accept it: far less data on slow mobile connections."""
    if (resp.status_code != 200 or "Content-Encoding" in resp.headers or resp.mimetype not in COMPRESSIBLE
            or "gzip" not in request.headers.get("Accept-Encoding", "")):
        return resp
    resp.direct_passthrough = False
    data = resp.get_data()
    if len(data) < 600:
        return resp
    resp.set_data(gzip.compress(data, 5))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers.add("Vary", "Accept-Encoding")
    return resp


_page_cache = {}  # (path, query, timezone) -> (made_at, html)


def cached_page(seconds):
    """Remember a page's HTML briefly, so a crowd watching the same scores costs one render, not hundreds."""
    def wrap(view):
        from functools import wraps

        @wraps(view)
        def inner(*args, **kwargs):
            key = (request.path, request.query_string, request.cookies.get("tz", "0"))
            hit = _page_cache.get(key)
            if hit and time.time() - hit[0] < seconds:
                return hit[1]
            html = view(*args, **kwargs)
            if len(_page_cache) > 300:
                _page_cache.clear()
            _page_cache[key] = (time.time(), html)
            return html
        return inner
    return wrap


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
        matchlinks.ensure_schema(conn)


def viewer_offset():
    """The viewer's UTC offset in minutes (Lagos = 60), sent by the page in a cookie."""
    try:
        return max(-840, min(840, int(request.cookies.get("tz", "0"))))
    except ValueError:
        return 0


def local_today(offset):
    return (datetime.now(timezone.utc) + timedelta(minutes=offset)).date()


# Shown first on the scores page, in this order, because most visitors follow Nigerian and African football.
TOP_LEAGUES = ["NPFL", "Africa Cup of Nations", "AFCON Qualifiers", "CAF Champions League", "CAF Confederation Cup",
               "World Cup Qualifiers (CAF)", "International Friendlies"]


def league_order(key):
    sport, league, _country = key
    sport_rank = SPORTS.index(sport) if sport in SPORTS else len(SPORTS)
    league_rank = TOP_LEAGUES.index(league) if league in TOP_LEAGUES else len(TOP_LEAGUES)
    return (sport_rank, league_rank, league)


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
    return OrderedDict(sorted(groups.items(), key=lambda kv: league_order(kv[0])))


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
        limit = {"basketball": 4.5, "hockey": 4.5, "tennis": 9}.get(r["sport"])  # hours; cricket Tests last days
        if limit and r["status"] == "live" and \
                (now - datetime.fromisoformat(r["kickoff"])).total_seconds() > limit * 3600:
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


def get_match_by_slug(slug):
    row = get_db().execute(MATCH_SQL + " WHERE m.slug = ?", (slug,)).fetchone()
    return decorate([row])[0] if row else None


def share_info(m):
    """Short score text and description used in the page title and share previews."""
    started = m["status"] in ("live", "finished") and m.get("home_score") is not None and m.get("away_score") is not None
    score = f"{m['home_score']}–{m['away_score']}" if started else "v"
    when = card.status_text(m).title() if m["status"] != "scheduled" else "Kick-off " + card.status_text(m)
    tail = "Live score, stats, line-ups and highlights on ScoreHub." if m["status"] != "scheduled" else \
           "Live score, line-ups and match centre on ScoreHub."
    return score, " ".join(f"{m['league']}: {m['home']} {score} {m['away']}. {when}. {tail}".split())


def match_url(m):
    """Permanent, readable link to a match page."""
    return url_for("match_page", slug=m["slug"] or str(m["id"]))


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
app.jinja_env.globals["match_url"] = match_url
app.jinja_env.globals["team_key"] = matchlinks.team_key
app.jinja_env.globals["site_url"] = SITE_URL
app.jinja_env.globals["adsense_client"] = ADSENSE_CLIENT
app.jinja_env.globals["contact_email"] = CONTACT_EMAIL


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
@cached_page(5)
def index():
    sport, day, league, live, offset = parse_args()
    return render_template("index.html", groups=load_groups(sport, day, league, offset, live),
                           sport=sport, day=day, league=league, live=live,
                           live_count=live_count(sport), nav=day_nav(day, offset),
                           leagues=league_chips(sport))


@app.route("/partial/myteams")
def partial_myteams():
    """Upcoming and live football for the followed teams and starred matches, over the next two weeks."""
    keys = {k for k in (request.args.get("teams") or "").split(",") if k}
    ids = {int(i) for i in (request.args.get("ids") or "").split(",") if i.isdigit()}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sql = MATCH_SQL + " WHERE l.sport = 'football' AND m.kickoff >= ? AND m.kickoff <= ? ORDER BY m.kickoff"
    rows = get_db().execute(sql, ((now - timedelta(hours=3)).isoformat(), (now + timedelta(days=14)).isoformat())).fetchall()
    rows = [r for r in rows if r["id"] in ids or matchlinks.team_key(r["home"]) in keys or matchlinks.team_key(r["away"]) in keys][:60]
    groups = OrderedDict()
    for row in decorate(rows):
        groups.setdefault((row["sport"], row["league"], row["country"]), []).append(row)
    groups = OrderedDict(sorted(groups.items(), key=lambda kv: league_order(kv[0])))
    return render_template("_matches.html", groups=groups, live_count=live_count("all"), show_date=True,
                           empty_text="No upcoming matches for your teams in the next two weeks.")


@app.route("/partial/matches")
@cached_page(5)
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


@app.route("/match/<slug>")
def match_page(slug):
    m = get_match_by_slug(slug)
    if m is None and slug.isdigit():  # an old numeric link: send it to the readable one
        old = get_match(int(slug))
        if old is None:
            abort(404)
        if old["slug"]:
            return redirect(match_url(old), code=301)
        m = old  # no readable link yet (should not happen): show the match as it is
    if m is None:
        abort(404)
    # "All matches" should return to the day the viewer sees this game on (their local day).
    kickoff = datetime.fromisoformat(m["kickoff"])
    back_date = (kickoff + timedelta(minutes=viewer_offset())).date().isoformat()
    share_score, share_desc = share_info(m)
    return render_template("match.html", m=m, detail=fetch_detail(m), hl=highlights.get(m),
                           back_date=back_date, share_score=share_score, share_desc=share_desc)


@app.route("/card/<slug>.png")
def match_card(slug):
    """The picture that appears when a match link is shared."""
    m = get_match_by_slug(slug)
    if m is None:
        abort(404)
    resp = Response(card.png_for(m, SITE_URL.split("://", 1)[-1]), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=%d" % (60 if m["status"] == "live" else 600)
    return resp


@app.route("/card/site.png")
def site_card():
    resp = Response(card.site_card(SITE_URL.split("://", 1)[-1]), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/partial/match/<int:match_id>")
def partial_match(match_id):
    """HTML fragment the match page polls to refresh the score and goals."""
    m = match_or_404(match_id)
    share_score, share_desc = share_info(m)
    return render_template("_match_body.html", m=m, detail=fetch_detail(m), hl=highlights.get(m),
                           share_score=share_score, share_desc=share_desc)


@app.route("/partial/highlights/<int:match_id>")
def partial_highlights(match_id):
    """Just the Highlights tab content; the page polls this until a video turns up."""
    m = match_or_404(match_id)
    hl = highlights.get(m)
    if hl is None:
        abort(404)
    return render_template("_highlights.html", hl=hl)


# ---- Pages that ad networks and visitors expect, plus crawler files ----

# Player search spends the shared API-Football allowance, so each visitor gets only a few searches a minute.
_player_hits = {}


def _player_rate_ok():
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    now = time.time()
    recent = [t for t in _player_hits.get(ip, []) if now - t < 60]
    if len(recent) >= 12:
        _player_hits[ip] = recent
        return False
    _player_hits[ip] = recent + [now]
    if len(_player_hits) > 2000:
        _player_hits.clear()
    return True


@app.route("/players")
def players_page():
    return render_template("players.html", enabled=bool(API_FOOTBALL_KEY))


@app.route("/api/players")
def api_players():
    if not API_FOOTBALL_KEY:
        return jsonify(players=[], error="off")
    if not _player_rate_ok():
        return jsonify(players=[], error="slow"), 429
    import apifootball
    players, error = apifootball.search_players(request.args.get("q", ""), API_FOOTBALL_KEY)
    return jsonify(players=players, error=error)


@app.route("/player/<int:pid>")
def player_page(pid):
    if not API_FOOTBALL_KEY:
        abort(404)
    if not _player_rate_ok():
        return render_template("player.html", p=None, error="slow"), 429
    import apifootball
    profile, error = apifootball.player_profile(pid, API_FOOTBALL_KEY)
    if error == "missing":
        abort(404)
    return render_template("player.html", p=profile, error=error), (200 if profile else 503)


# Pages with little text stay reachable but are not listed, linked or put in the sitemap: thin pages hurt more than they help.
MIN_GUIDE_WORDS = 100
LEAGUES_LISTED = {k: g for k, g in content.LEAGUES.items() if len(" ".join(g["body"]).split()) >= MIN_GUIDE_WORDS}
LEAGUE_GUIDE = {name: slug for slug, g in LEAGUES_LISTED.items() for name in g["names"]}
app.jinja_env.globals["league_guide"] = lambda name: LEAGUE_GUIDE.get(name)


def _paragraphs(blocks):
    return [("h" if b.startswith("## ") else "p", b[3:] if b.startswith("## ") else b) for b in blocks]


@app.route("/guides")
def guides_page():
    return render_template("guides.html", leagues=LEAGUES_LISTED, articles=content.ARTICLES)


@app.route("/guides/<slug>")
def article_page(slug):
    a = content.ARTICLES.get(slug)
    if not a:
        abort(404)
    others = [(k, v["title"]) for k, v in content.ARTICLES.items() if k != slug][:4]
    return render_template("article.html", a=a, blocks=_paragraphs(a["body"]), slug=slug, others=others)


@app.route("/league/<slug>")
def league_page(slug):
    g = content.LEAGUES.get(slug)
    if not g:
        abort(404)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    marks = ",".join("?" * len(g["names"]))
    rows = get_db().execute(MATCH_SQL + f" WHERE l.name IN ({marks}) AND m.kickoff >= ? AND m.kickoff <= ? ORDER BY m.kickoff LIMIT 10",
                            (*g["names"], (now - timedelta(hours=3)).isoformat(), (now + timedelta(days=7)).isoformat())).fetchall()
    upcoming = decorate(rows)
    table = next((code_for(n) for n in g["names"] if code_for(n)), None)
    return render_template("league_guide.html", g=g, slug=slug, upcoming=upcoming, table=table, blocks=g["body"], listed=slug in LEAGUES_LISTED)


@app.route("/following")
def following_page():
    return render_template("following.html")


@app.route("/api/teams")
def api_teams():
    """Team search for the Following page. With no search text: teams playing soon in the leagues we put first."""
    q = (request.args.get("q") or "").strip()[:40].replace("\\", "").replace("%", "").replace("_", "").strip()
    if not q and (request.args.get("q") or "").strip():
        q = "?"  # the text was only symbols: no results, not "everything"
    db = get_db()
    if len(q) >= 2:
        like = "%" + q + "%"
        rows = db.execute("""SELECT t.name, t.crest, COUNT(*) AS c FROM teams t
                             JOIN matches m ON t.id IN (m.home_id, m.away_id)
                             JOIN leagues l ON l.id = m.league_id
                             WHERE l.sport = 'football' AND t.name LIKE ? GROUP BY t.id ORDER BY t.name LIKE ? DESC, c DESC, t.name LIMIT 15""",
                          (like, q + "%")).fetchall()
    elif q:
        rows = []
    else:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        found = db.execute(MATCH_SQL + """ WHERE l.sport = 'football' AND m.kickoff >= ? AND m.kickoff <= ?
                                          ORDER BY m.kickoff""",
                           ((now - timedelta(hours=2)).isoformat(), (now + timedelta(days=4)).isoformat())).fetchall()
        found.sort(key=lambda r: league_order((r["sport"], r["league"], r["country"])))  # stable: keeps kickoff order inside a league
        rows, seen = [], set()
        for r in found:
            for name, crest in ((r["home"], r["home_crest"]), (r["away"], r["away_crest"])):
                if name not in seen and len(rows) < 24:
                    seen.add(name)
                    rows.append({"name": name, "crest": crest})
    return jsonify(teams=[{"name": r["name"], "crest": r["crest"], "key": matchlinks.team_key(r["name"])} for r in rows])


@app.route("/about")
def about_page():
    return render_template("about.html")


@app.route("/privacy")
def privacy_page():
    return render_template("privacy.html")


@app.route("/terms")
def terms_page():
    return render_template("terms.html")


@app.route("/contact")
def contact_page():
    return render_template("contact.html")


@app.route("/ads.txt")
def ads_txt():
    """Tells ad buyers that this publisher id may sell this site's ad space (needed once AdSense is set up)."""
    if not ADSENSE_CLIENT:
        abort(404)
    pub = ADSENSE_CLIENT.replace("ca-pub-", "pub-")
    return Response(f"google.com, {pub}, DIRECT, f08c47fec0942fa0\n", mimetype="text/plain")


@app.route("/robots.txt")
def robots_txt():
    return Response(f"User-agent: *\nAllow: /\nDisallow: /partial/\nDisallow: /api/\n\nSitemap: {SITE_URL}/sitemap.xml\n",
                    mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    paths = ["/", "/?sport=football", "/?sport=basketball", "/?sport=hockey", "/?sport=tennis", "/?sport=cricket",
             "/about", "/privacy", "/terms", "/contact"]
    now = datetime.now(timezone.utc)
    rows = get_db().execute(
        "SELECT slug, kickoff FROM matches WHERE slug IS NOT NULL AND kickoff BETWEEN ? AND ? ORDER BY kickoff DESC LIMIT 1000",
        ((now - timedelta(days=3)).isoformat(), (now + timedelta(days=3)).isoformat())).fetchall()
    paths += [f"/match/{r['slug']}" for r in rows]
    paths += ["/guides"] + [f"/guides/{k}" for k in content.ARTICLES] + [f"/league/{k}" for k in LEAGUES_LISTED]
    body = "".join(f"<url><loc>{SITE_URL}{p.replace('&', '&amp;')}</loc></url>" for p in paths)
    return Response('<?xml version="1.0" encoding="UTF-8"?>'
                    f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
                    mimetype="application/xml")


@app.route("/sw.js")
def service_worker():
    """Must be served from the site root so it can receive pushes for every page."""
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript", max_age=0)
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
    teams = body.get("teams") if isinstance(body.get("teams"), list) else []
    push.save({"endpoint": sub["endpoint"], "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}, matches, teams)
    return jsonify(ok=True)


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    endpoint = (request.get_json(silent=True) or {}).get("endpoint", "")
    if endpoint:
        push.remove(endpoint)
    return jsonify(ok=True)


def _rows_for_push():
    """Football matches from the last few hours to the next half hour: enough to spot kick-offs, goals, full-times."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sql = MATCH_SQL + " WHERE l.sport = 'football' AND m.kickoff >= ? AND m.kickoff <= ?"
    params = [(now - timedelta(hours=4)).isoformat(), (now + timedelta(minutes=push.REMIND_MINUTES + 1)).isoformat()]
    with app.app_context():
        return decorate(get_db().execute(sql, params).fetchall())


def start_push():
    """Background goal watcher (one per process; the site runs a single worker)."""
    if not push.enabled():
        print("push: VAPID keys not set, notifications are off")
        return
    import threading
    if API_FOOTBALL_KEY:
        import apifootball
        push.SCORER_LOOKUP = lambda row, side: apifootball.latest_goal(
            row, API_FOOTBALL_KEY, side, int(row.get("home_score" if side == "home" else "away_score") or 0))
    threading.Thread(target=push.run, args=(_rows_for_push,), daemon=True).start()


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
        kwargs={"extras": (sync.EspnSoccerProvider(), sync.EspnBasketballProvider(), sync.EspnHockeyProvider(),
                                       sync.EspnTennisProvider(), sync.EspnCricketProvider())}, daemon=True
    ).start()


if os.environ.get("RUN_SYNC") == "1":
    start_sync()


if __name__ == "__main__":
    if not FOOTBALL_DATA_KEY:
        print("Note: FOOTBALL_DATA_KEY not found (.env file or terminal). League tables will be unavailable.")
    app.run(debug=True)