"""Sync layer: pulls matches from a data provider and upserts them into SQLite.

Providers all expose fetch_matches() and return a list of dicts shaped like:
    {
      "external_id": "abc123",
      "sport": "football" | "basketball" | "hockey" | "tennis" | "cricket",
      "league": "Premier League", "country": "England",
      "home": "Arsenal", "away": "Chelsea",
      "kickoff": "2026-10-09T19:00:00+00:00",
      "status": "scheduled" | "live" | "finished" | "postponed",
      "home_score": 1, "away_score": 0,
      "period": "2H", "clock": "63'",
    }

DemoProvider lets the site run end to end with no API key. To go real, write a
provider class for your chosen API that maps its response into this shape and
pass it to run_forever().
"""
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

from config import load_env

load_env()  # reads FOOTBALL_DATA_KEY from the .env file, if there is one

DB_PATH = os.path.join(os.path.dirname(__file__), "scorehub.db")


class DemoProvider:
    """Generates fake fixtures around 'now' so the UI has something to show."""

    FIXTURES = [
        ("football", "Premier League", "England", "Arsenal", "Chelsea"),
        ("football", "Premier League", "England", "Liverpool", "Man City"),
        ("football", "La Liga", "Spain", "Real Madrid", "Barcelona"),
        ("football", "La Liga", "Spain", "Sevilla", "Valencia"),
        ("basketball", "NBA", "USA", "Lakers", "Celtics"),
        ("basketball", "NBA", "USA", "Warriors", "Bucks"),
    ]

    def __init__(self):
        self.seed = datetime.now(timezone.utc).strftime("%Y%m%d")

    def fetch_matches(self):
        now = datetime.now(timezone.utc)
        out = []
        for i, (sport, league, country, home, away) in enumerate(self.FIXTURES):
            rng = random.Random(f"{self.seed}-{i}")
            kickoff = now - timedelta(minutes=rng.choice([-90, -30, 20, 55, 130, 400]))
            elapsed = (now - kickoff).total_seconds() / 60
            length = 95 if sport == "football" else 150
            if elapsed < 0:
                status, hs, as_, period, clock = "scheduled", None, None, None, None
            elif elapsed > length:
                status = "finished"
                hs, as_ = self._score(rng, sport, 1.0)
                period, clock = "FT", None
            else:
                status = "live"
                hs, as_ = self._score(rng, sport, elapsed / length)
                period, clock = self._clock(sport, elapsed)
            out.append({
                "external_id": f"demo-{self.seed}-{i}",
                "sport": sport, "league": league, "country": country,
                "home": home, "away": away,
                "kickoff": kickoff.isoformat(), "status": status,
                "home_score": hs, "away_score": as_,
                "period": period, "clock": clock,
            })
        return out

    @staticmethod
    def _score(rng, sport, progress):
        top = 4 if sport == "football" else 120
        return (int(rng.random() * top * progress), int(rng.random() * top * progress))

    @staticmethod
    def _clock(sport, elapsed):
        if sport == "football":
            return ("1H" if elapsed <= 45 else "2H"), f"{min(int(elapsed), 90)}'"
        quarter = min(int(elapsed // 36) + 1, 4)
        return f"Q{quarter}", f"{12 - int(elapsed % 36 / 3):02d}:00"


class FootballDataProvider:
    """Real football fixtures and live scores from football-data.org (free tier).

    One request per poll covers every competition your key can see: Premier League,
    La Liga, Serie A, Bundesliga, Ligue 1, Champions League and a few more.
    The free plan allows 10 requests per minute, so keep the sync interval at 30s or more.
    """

    URL = "https://api.football-data.org/v4/matches"
    STATUS = {
        "SCHEDULED": "scheduled", "TIMED": "scheduled",
        "IN_PLAY": "live", "PAUSED": "live",
        "FINISHED": "finished", "AWARDED": "finished",
        "POSTPONED": "postponed", "SUSPENDED": "postponed", "CANCELLED": "postponed",
    }

    def __init__(self, api_key):
        self.headers = {"X-Auth-Token": api_key}

    def fetch_range(self, days_back, days_ahead, chunk_days=5):
        """Matches from days_back days ago to days_ahead days from now.

        Long ranges are split into short requests (APIs often cap the date span),
        with a one-day overlap so no day is missed. Duplicates are merged by id.
        """
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days_back)
        end = today + timedelta(days=days_ahead)
        found = {}
        while start <= end:
            stop = min(start + timedelta(days=chunk_days - 1), end)
            resp = requests.get(
                self.URL,
                headers=self.headers,
                params={"dateFrom": start.isoformat(), "dateTo": stop.isoformat()},
                timeout=15,
            )
            resp.raise_for_status()
            for m in resp.json().get("matches", []):
                found[m["id"]] = self._convert(m)
            if stop >= end:
                break
            start = stop  # overlap by one day
        return list(found.values())

    def fetch_matches(self):
        """Yesterday to tomorrow: the cheap request used for frequent live updates."""
        return self.fetch_range(1, 1)

    def _convert(self, m):
        status = self.STATUS.get(m.get("status"), "scheduled")
        minute = m.get("minute")
        period = clock = None
        if m.get("status") == "PAUSED":
            period = "HT"
        elif status == "live":
            period = "1H" if (minute or 0) <= 45 else "2H"
            clock = f"{minute}'" if minute else None
        elif status == "finished":
            period = "FT"

        full = (m.get("score") or {}).get("fullTime") or {}
        home_team, away_team = m["homeTeam"], m["awayTeam"]
        kickoff = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        return {
            "external_id": f"fd-{m['id']}",
            "sport": "football",
            "league": m["competition"]["name"],
            "country": (m.get("area") or {}).get("name"),
            "home": home_team.get("shortName") or home_team["name"],
            "away": away_team.get("shortName") or away_team["name"],
            "home_crest": home_team.get("crest"),
            "away_crest": away_team.get("crest"),
            "kickoff": kickoff.astimezone(timezone.utc).isoformat(),
            "status": status,
            "home_score": full.get("home"),
            "away_score": full.get("away"),
            "period": period,
            "clock": clock,
        }


class EspnProvider:
    """Scores from ESPN's public scoreboard feed (no key needed, one request per league per day).

    An unofficial feed, so if ESPN changes it that sport may go quiet while the others carry on.
    Subclasses set SPORT, LEAGUES, REGULATION (number of normal periods) and PREFIX.
    """

    SPORT = ""
    LEAGUES = []          # (espn slug, display name, country)
    REGULATION = 4        # periods before overtime
    PREFIX = "Q"          # Q1.. for basketball, P1.. for hockey
    URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard"

    def fetch_range(self, days_back, days_ahead):
        """One request per league per day: ESPN's plain ?dates=YYYYMMDD form (ranges get a 400)."""
        today = datetime.now(timezone.utc).date()
        found = {}
        for slug, league, country in self.LEAGUES:
            for offset in range(-min(days_back, 3), days_ahead + 1):
                day = today + timedelta(days=offset)
                try:
                    resp = requests.get(self.URL.format(sport=self.SPORT, slug=slug), params={"dates": f"{day:%Y%m%d}"},
                                        headers={"User-Agent": "Mozilla/5.0 ScoreHub"}, timeout=15)
                    resp.raise_for_status()
                    for event in resp.json().get("events", []):
                        m = self._convert(event, league, country)
                        if m:
                            found[m["external_id"]] = m
                except Exception as exc:
                    print(f"{self.SPORT} sync ({slug} {day}) failed: {exc}")
        return list(found.values())

    def fetch_matches(self):
        return self.fetch_range(1, 1)

    def _period_label(self, n, detail=""):
        if not n:
            return None
        if n <= self.REGULATION:
            return f"{self.PREFIX}{n}"
        if "SO" in detail.upper().replace("SOON", "").split() or "shootout" in detail.lower():
            return "SO"
        extra = n - self.REGULATION
        return "OT" if extra == 1 else f"{extra}OT"

    def _convert(self, event, league, country):
        comp = (event.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in teams or "away" not in teams:
            return None
        st = event.get("status") or {}
        kind = st.get("type") or {}
        name, state = kind.get("name", ""), kind.get("state", "pre")
        detail = (kind.get("shortDetail") or kind.get("detail") or "").replace("/", " ")
        period_n = st.get("period") or 0
        status, period, clock = "scheduled", None, None
        if name in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_SUSPENDED"):
            status = "postponed"
        elif state == "post":
            status = "finished"
            period = "FT" if period_n <= self.REGULATION else self._period_label(period_n, detail)
        elif state == "in":
            status = "live"
            if name == "STATUS_HALFTIME":
                period = "HT"
            else:
                period = self._period_label(period_n, detail)
                clock = "End" if name == "STATUS_END_PERIOD" else (st.get("displayClock") or None)

        def score(team):
            try:
                return int(team.get("score"))
            except (TypeError, ValueError):
                return None

        def team_name(team):
            t = team.get("team") or {}
            return t.get("shortDisplayName") or t.get("displayName") or t.get("name") or "TBD"

        kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        played = status in ("live", "finished")
        return {
            "external_id": f"espn-{self.SPORT}-{event['id']}",
            "sport": self.SPORT,
            "league": league, "country": country,
            "home": team_name(teams["home"]), "away": team_name(teams["away"]),
            "home_crest": (teams["home"].get("team") or {}).get("logo"),
            "away_crest": (teams["away"].get("team") or {}).get("logo"),
            "kickoff": kickoff.astimezone(timezone.utc).isoformat(),
            "status": status,
            "home_score": score(teams["home"]) if played else None,
            "away_score": score(teams["away"]) if played else None,
            "period": period, "clock": clock,
        }


class EspnBasketballProvider(EspnProvider):
    SPORT = "basketball"
    LEAGUES = [("nba", "NBA", "USA"), ("wnba", "WNBA", "USA")]


class EspnHockeyProvider(EspnProvider):
    SPORT = "hockey"
    LEAGUES = [("nhl", "NHL", "USA & Canada")]
    REGULATION = 3
    PREFIX = "P"


class EspnTennisProvider(EspnProvider):
    """ATP and WTA singles. A tennis 'score' is sets won; live matches show the set and its games."""

    SPORT = "tennis"
    LEAGUES = [("atp", "ATP", "Men"), ("wta", "WTA", "Women")]
    EVERY = 60

    def fetch_range(self, days_back, days_ahead):
        today = datetime.now(timezone.utc).date()
        found = {}
        for slug, tour, _ in self.LEAGUES:
            for offset in range(-min(days_back, 2), min(days_ahead, 4) + 1):
                day = today + timedelta(days=offset)
                try:
                    resp = requests.get(self.URL.format(sport="tennis", slug=slug), params={"dates": f"{day:%Y%m%d}"},
                                        headers={"User-Agent": "Mozilla/5.0 ScoreHub"}, timeout=15)
                    resp.raise_for_status()
                    for event in resp.json().get("events", []):
                        for group in event.get("groupings") or []:
                            if "singles" not in ((group.get("grouping") or {}).get("displayName") or "").lower():
                                continue
                            for comp in group.get("competitions") or []:
                                m = self._convert_match(comp, event.get("name") or tour, tour)
                                if m:
                                    found[m["external_id"]] = m
                except Exception as exc:
                    print(f"tennis sync ({slug} {day}) failed: {exc}")
        return list(found.values())

    def fetch_matches(self):
        return self.fetch_range(1, 1)

    def _convert_match(self, comp, tournament, tour):
        players = sorted(comp.get("competitors") or [], key=lambda c: c.get("order", 0))
        if len(players) != 2:
            return None
        a, b = players
        st = comp.get("status") or {}
        kind = st.get("type") or {}
        name, state = kind.get("name", ""), kind.get("state", "pre")
        status = "scheduled"
        if name in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_SUSPENDED"):
            status = "postponed"
        elif state == "post":
            status = "finished"
        elif state == "in":
            status = "live"

        def sets_won(p):
            return sum(1 for ls in p.get("linescores") or [] if ls.get("winner"))

        def pname(p):
            ath = p.get("athlete") or {}
            return ath.get("displayName") or ath.get("shortName") or "TBD"

        def flag(p):
            return ((p.get("athlete") or {}).get("flag") or {}).get("href")

        played = status in ("live", "finished")
        period = clock = None
        if status == "finished":
            period = "FT"
        elif status == "live":
            sets_a, sets_b = a.get("linescores") or [], b.get("linescores") or []
            n = max(len(sets_a), len(sets_b))
            period = f"Set {n}" if n else "Live"
            if n:
                ga = int(sets_a[-1].get("value", 0)) if sets_a else 0
                gb = int(sets_b[-1].get("value", 0)) if sets_b else 0
                clock = f"{ga}-{gb}"
        kickoff = datetime.fromisoformat((comp.get("date") or comp.get("startDate")).replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        return {
            "external_id": f"espn-tennis-{comp['id']}", "sport": "tennis",
            "league": tournament, "country": tour,
            "home": pname(a), "away": pname(b), "home_crest": flag(a), "away_crest": flag(b),
            "kickoff": kickoff.astimezone(timezone.utc).isoformat(), "status": status,
            "home_score": sets_won(a) if played else None, "away_score": sets_won(b) if played else None,
            "period": period, "clock": clock,
        }


class EspnCricketProvider(EspnProvider):
    """Cricket from ESPN's 'scoreboard header' feed, which lists every active series in one request.

    A cricket 'score' is text such as "311 & 172/9 (53 ov)", stored as-is.
    """

    SPORT = "cricket"
    EVERY = 60
    URL = "https://site.web.api.espn.com/apis/v2/scoreboard/header"

    @staticmethod
    def _tidy(score):
        score = re.sub(r",\s*target \d+", "", score or "")
        return re.sub(r"\((\d+(?:\.\d)?)/\d+ ov\)", r"(\1 ov)", score).strip()

    def fetch_range(self, days_back, days_ahead):
        found = {}
        today = datetime.now(timezone.utc).date()
        days = [None] if days_back <= 1 and days_ahead <= 1 else \
            [None] + [today + timedelta(days=d) for d in range(-2, 4)]
        for day in days:
            params = {"sport": "cricket", "lang": "en", "region": "us"}
            if day:
                params["dates"] = f"{day:%Y%m%d}"
            try:
                resp = requests.get(self.URL, params=params, headers={"User-Agent": "Mozilla/5.0 ScoreHub"}, timeout=15)
                resp.raise_for_status()
                for sport in resp.json().get("sports", []):
                    for league in sport.get("leagues", []):
                        for event in league.get("events", []):
                            m = self._convert(event, league.get("name") or "Cricket", None)
                            if m:
                                found[m["external_id"]] = m
            except Exception as exc:
                print(f"cricket sync ({day}) failed: {exc}")
        return list(found.values())

    def fetch_matches(self):
        return self.fetch_range(1, 1)

    def _convert(self, event, league, country):
        teams = {c.get("homeAway"): c for c in event.get("competitors") or []}
        if "home" not in teams or "away" not in teams:
            return None
        full = (event.get("fullStatus") or {}).get("type") or {}
        state = event.get("status") or full.get("state") or "pre"
        description = (full.get("description") or "").lower()
        status, period, clock = "scheduled", None, None
        if any(w in description for w in ("postponed", "cancel")):
            status = "postponed"
        elif state == "post":
            status, period = "finished", "Result"
        elif state == "in":
            status, clock = "live", "Live"
            n = event.get("period") or 0
            if n:
                period = f"Day {n}" if "test" in (event.get("name") or "").lower() else f"Inns {n}"
        kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        played = status in ("live", "finished")
        return {
            "external_id": f"espn-cricket-{event['id']}", "sport": "cricket",
            "league": league, "country": country,
            "home": teams["home"].get("displayName") or "TBD", "away": teams["away"].get("displayName") or "TBD",
            "home_crest": teams["home"].get("logo"), "away_crest": teams["away"].get("logo"),
            "kickoff": kickoff.astimezone(timezone.utc).isoformat(), "status": status,
            "home_score": (self._tidy(teams["home"].get("score")) or None) if played else None,
            "away_score": (self._tidy(teams["away"].get("score")) or None) if played else None,
            "period": period, "clock": clock,
        }


def upsert(conn, m):
    conn.execute(
        "INSERT OR IGNORE INTO leagues (sport, name, country) VALUES (?, ?, ?)",
        (m["sport"], m["league"], m.get("country")),
    )
    league_id = conn.execute(
        "SELECT id FROM leagues WHERE sport = ? AND name = ?", (m["sport"], m["league"])
    ).fetchone()[0]

    team_ids = []
    for name, crest in ((m["home"], m.get("home_crest")), (m["away"], m.get("away_crest"))):
        conn.execute("INSERT OR IGNORE INTO teams (name) VALUES (?)", (name,))
        if crest:
            conn.execute("UPDATE teams SET crest = ? WHERE name = ?", (crest, name))
        team_ids.append(conn.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()[0])

    conn.execute(
        """
        INSERT INTO matches (external_id, league_id, home_id, away_id, kickoff, status,
                             home_score, away_score, period, clock, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(external_id) DO UPDATE SET
            kickoff=excluded.kickoff, status=excluded.status,
            home_score=excluded.home_score, away_score=excluded.away_score,
            period=excluded.period, clock=excluded.clock, updated_at=excluded.updated_at
        """,
        (m["external_id"], league_id, team_ids[0], team_ids[1], m["kickoff"], m["status"],
         m["home_score"], m["away_score"], m["period"], m["clock"],
         datetime.now(timezone.utc).isoformat()),
    )


def save_matches(matches):
    with sqlite3.connect(DB_PATH) as conn:
        for m in matches:
            upsert(conn, m)
    return len(matches)


def sync_once(provider):
    return save_matches(provider.fetch_matches())


def run_forever(provider, interval=30):
    while True:
        try:
            n = sync_once(provider)
            print(f"synced {n} matches")
        except Exception as exc:  # keep the worker alive on provider errors
            print("sync failed:", exc)
        time.sleep(interval)


_last_run = {}


def run_football(provider, interval=30, full_every=1800, days_back=7, days_ahead=7, extras=()):
    """Live scores every `interval` seconds, plus a full -7/+7 day refresh every 30 minutes.

    Per minute this uses about 2 requests, plus about 4 more once every 30 minutes,
    well inside the free plan's 10 requests per minute.
    """
    last_full = 0.0
    while True:
        full = time.time() - last_full >= full_every
        for p in [provider, *extras]:
            if p is None:
                continue
            if not full and time.time() - _last_run.get(id(p), 0) < getattr(p, "EVERY", 0):
                continue  # slower sources are polled less often
            _last_run[id(p)] = time.time()
            try:  # one provider failing must not stop the others
                n = save_matches(p.fetch_range(days_back, days_ahead) if full else p.fetch_matches())
                print(f"{'full' if full else 'live'} sync ({type(p).__name__}): {n} matches")
            except Exception as exc:
                print(f"sync failed ({type(p).__name__}):", exc)
        if full:
            last_full = time.time()
        time.sleep(interval)


def clear_demo_data():
    """Remove the fake fixtures so only real matches are shown."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM matches WHERE external_id LIKE 'demo-%'")


if __name__ == "__main__":
    api_key = os.environ.get("FOOTBALL_DATA_KEY")
    if api_key:
        print("Using football-data.org (real scores)")
        clear_demo_data()
        run_football(FootballDataProvider(api_key))
    else:
        print("FOOTBALL_DATA_KEY not set, using demo data")
        run_forever(DemoProvider())