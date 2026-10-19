"""Web push: remember who wants goal alerts, spot goals, and send the notifications.

Needs VAPID keys (run `python genkeys.py`, then set VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY).
Without them the feature simply stays switched off.

Subscriptions live in a small SQLite file. On Railway the main database is rebuilt on every
deploy, so point SUBS_DB_PATH at a volume (e.g. /data/subs.db) to keep them; either way the
browser re-registers itself each time the site is opened, so nothing is lost for long.
"""
import json
import os
import sqlite3
import threading
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import matchlinks

VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_PUBLIC = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@scorehub.local")
SUBS_DB = os.environ.get("SUBS_DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "subs.db")
CHECK_EVERY = int(os.environ.get("GOAL_CHECK_SECONDS", "15"))

# Only real browser push services are accepted as endpoints (stops anyone aiming us at other sites).
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.apple.com",
              "notify.windows.com", "push.services.mozilla.com")

_lock = threading.Lock()


def enabled():
    return bool(VAPID_PRIVATE and VAPID_PUBLIC)


def _db():
    con = sqlite3.connect(SUBS_DB, timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS subs (endpoint TEXT PRIMARY KEY, sub TEXT NOT NULL,"
                " matches TEXT NOT NULL DEFAULT '[]', updated REAL)")
    have = [r[1] for r in con.execute("PRAGMA table_info(subs)")]
    for col, ddl in (("teams", "TEXT NOT NULL DEFAULT '[]'"), ("tz", "INTEGER NOT NULL DEFAULT 60"),
                     ("digest", "INTEGER NOT NULL DEFAULT 1"), ("last_digest", "TEXT")):
        if col not in have:
            con.execute(f"ALTER TABLE subs ADD COLUMN {col} {ddl}")
    return con


def valid_endpoint(url):
    try:
        u = urlparse(url)
    except Exception:
        return False
    host = u.hostname or ""
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def save(sub, matches, teams=(), tz=None, digest=True):
    """Store (or refresh) one browser's subscription, the match ids it starred and the teams it follows.

    tz is the browser's UTC offset in minutes (Lagos = 60), used to send the morning summary at a sensible hour."""
    tz = tz if isinstance(tz, int) and -840 <= tz <= 840 else 60
    ids = sorted({int(x) for x in matches if str(x).isdigit()})[:300]
    keys = sorted({t for t in teams if isinstance(t, str) and re.fullmatch(r"[a-z0-9-]{1,60}", t)})[:100]
    with _lock, _db() as con:
        con.execute("INSERT INTO subs (endpoint, sub, matches, teams, tz, digest, updated) VALUES (?,?,?,?,?,?,?)"
                    " ON CONFLICT(endpoint) DO UPDATE SET sub=excluded.sub, matches=excluded.matches,"
                    " teams=excluded.teams, tz=excluded.tz, digest=excluded.digest, updated=excluded.updated",
                    (sub["endpoint"], json.dumps(sub), json.dumps(ids), json.dumps(keys), tz, 1 if digest else 0, time.time()))


def remove(endpoint):
    with _lock, _db() as con:
        con.execute("DELETE FROM subs WHERE endpoint = ?", (endpoint,))


def subscribers_for(row):
    """Browsers that starred this match or follow either of its teams."""
    with _lock, _db() as con:
        rows = con.execute("SELECT sub, matches, teams FROM subs").fetchall()
    keys = {matchlinks.team_key(row.get("home", "")), matchlinks.team_key(row.get("away", ""))}
    return [json.loads(s) for s, m, t in rows if row["id"] in json.loads(m) or keys & set(json.loads(t))]


def send(sub, payload):
    """Send one notification. Returns False when the subscription is dead and should be dropped."""
    from pywebpush import webpush, WebPushException
    try:
        webpush(subscription_info=sub, data=json.dumps(payload), vapid_private_key=VAPID_PRIVATE,
                vapid_claims={"sub": VAPID_SUBJECT}, ttl=600)
        return True
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return False
        print(f"push: send failed ({status}): {exc}")
        return True
    except Exception as exc:
        print(f"push: send failed: {exc}")
        return True


def _score(row):
    return int(row.get("home_score") or 0), int(row.get("away_score") or 0)


SCORER_LOOKUP = None  # set by the app: function(row, side) -> {"player", "minute", "kind", "assist"} or None


def _scorer_text(sc):
    if not sc or not sc.get("player"):
        return ""
    text = f"Own goal by {sc['player']}" if sc.get("kind") == "og" else sc["player"]
    if sc.get("minute"):
        text += f" {sc['minute']}"
    if sc.get("kind") == "pen":
        text += " (pen)"
    if sc.get("assist") and sc.get("kind") != "og":
        text += f" · assist: {sc['assist']}"
    return text


def goal_payload(row, side, scorer=None):
    hs, as_ = _score(row)
    team = row["home"] if side == "home" else row["away"]
    minute = f" · {row['clock']}" if row.get("clock") else ""
    return {"title": f"⚽ GOAL! {row['home']} {hs}–{as_} {row['away']}",
            "body": _scorer_text(scorer) or f"{team} scored{minute}", "url": _url(row), "tag": f"goal-{row['id']}"}


def _url(row):
    return f"/match/{row.get('slug') or row['id']}"


REMIND_MINUTES = 30


def detect_events(state, reminded, rows, now=None):
    """Work out what is worth a notification: goal, kickoff, fulltime, soon.

    Returns a list of (row, kind, side). A match seen for the first time only sets a baseline, so a restart
    never replays old goals, kick-offs or results. A "soon" reminder is sent once per match.
    """
    now = now or datetime.now(timezone.utc)
    events = []
    for row in rows:
        score, status = _score(row), row.get("status")
        before = state.get(row["id"])
        state[row["id"]] = (score, status)
        if status == "scheduled" and row["id"] not in reminded:
            try:
                mins = (datetime.fromisoformat(row["kickoff"]) - now).total_seconds() / 60
            except Exception:
                mins = None
            if mins is not None and 0 < mins <= REMIND_MINUTES:
                reminded.add(row["id"])
                events.append((row, "soon", None))
        if before is None:
            continue
        old_score, old_status = before
        if old_status == "scheduled" and status == "live":
            events.append((row, "kickoff", None))
        if status in ("live", "finished") and old_status in ("live", "finished"):
            for i, side in enumerate(("home", "away")):
                if score[i] > old_score[i]:
                    events.append((row, "goal", side))
        if old_status == "live" and status == "finished":
            events.append((row, "fulltime", None))
    return events


def event_payload(row, kind, side=None, now=None, scorer=None):
    if kind == "goal":
        return goal_payload(row, side, scorer)
    hs, as_ = _score(row)
    if kind == "kickoff":
        return {"title": f"🟢 Kick-off: {row['home']} v {row['away']}", "body": row.get("league", ""),
                "url": _url(row), "tag": f"kickoff-{row['id']}"}
    if kind == "fulltime":
        return {"title": f"🏁 Full time: {row['home']} {hs}–{as_} {row['away']}", "body": "Tap for the match summary",
                "url": _url(row), "tag": f"ft-{row['id']}"}
    now = now or datetime.now(timezone.utc)
    mins = max(1, round((datetime.fromisoformat(row["kickoff"]) - now).total_seconds() / 60))
    return {"title": f"⏰ Starting soon: {row['home']} v {row['away']}", "body": f"Kicks off in about {mins} minutes",
            "url": _url(row), "tag": f"soon-{row['id']}"}


def _deliver(row, kind, side, sender, subs=None):
    """Send one event to everyone who follows it. For goals, first try to find out who scored."""
    subs = subs if subs is not None else subscribers_for(row)
    if not subs:
        return
    scorer = None
    if kind == "goal" and SCORER_LOOKUP:
        for attempt in range(2):
            try:
                scorer = SCORER_LOOKUP(row, side)
            except Exception as exc:
                print(f"push: scorer lookup failed: {exc}")
            if scorer:
                break
            if attempt == 0:
                time.sleep(10)  # the data provider is often a few seconds behind
    payload = event_payload(row, kind, side, scorer=scorer)
    for sub in subs:
        if not sender(sub, payload):
            remove(sub["endpoint"])


def notify_events(events, sender=send):
    for row, kind, side in events:
        subs = subscribers_for(row)  # nobody follows it: no scorer lookup, no API request spent
        if kind == "goal" and SCORER_LOOKUP and subs:
            threading.Thread(target=_deliver, args=(row, kind, side, sender, subs), daemon=True).start()
        else:
            _deliver(row, kind, side, sender, subs)


DIGEST_ROWS = None   # set by the app: function() -> football matches not yet finished, from 12 h ago to 40 h ahead
DIGEST_HOUR = 8      # local time; sent between 8:00 and 11:59, once a day


def _digest_payload(mine, tz):
    def when(r):
        return (datetime.fromisoformat(r["kickoff"]) + timedelta(minutes=tz)).strftime("%H:%M")
    if len(mine) == 1:
        r = mine[0]
        return {"title": f"⚽ Today: {r['home']} v {r['away']}", "body": f"Kicks off at {when(r)} · {r.get('league', '')}".strip(" ·"),
                "url": _url(r), "tag": "digest"}
    lines = [f"{r['home']} v {r['away']} {when(r)}" for r in mine[:3]]
    if len(mine) > 3:
        lines.append(f"+{len(mine) - 3} more")
    return {"title": f"⚽ {len(mine)} matches for your teams today", "body": " · ".join(lines), "url": "/?sport=football", "tag": "digest"}


def send_digests(rows, now=None, sender=send):
    """Once a morning, tell each person which of their teams' (or starred) matches are on today. Silent on days with none."""
    now = now or datetime.now(timezone.utc)
    with _lock, _db() as con:
        subs = con.execute("SELECT endpoint, sub, matches, teams, tz, last_digest FROM subs WHERE digest = 1").fetchall()
    keyed = [(r, {matchlinks.team_key(r.get("home", "")), matchlinks.team_key(r.get("away", ""))}) for r in rows]
    sent = 0
    for endpoint, sub_json, m_json, t_json, tz, last in subs:
        local = now + timedelta(minutes=tz)
        today = local.date().isoformat()
        if last == today or not (DIGEST_HOUR <= local.hour < DIGEST_HOUR + 4):
            continue
        with _lock, _db() as con:  # mark first: a person is never sent two summaries in a day, even if a send fails
            con.execute("UPDATE subs SET last_digest = ? WHERE endpoint = ?", (today, endpoint))
        ids, teams = set(json.loads(m_json)), set(json.loads(t_json))
        if not ids and not teams:
            continue
        start = datetime(local.year, local.month, local.day, tzinfo=timezone.utc) - timedelta(minutes=tz)
        end = start + timedelta(days=1)
        mine = [r for r, keys in keyed if r.get("status") in ("scheduled", "live")
                and start <= datetime.fromisoformat(r["kickoff"]) < end and (r["id"] in ids or keys & teams)]
        if not mine:
            continue
        mine.sort(key=lambda r: r["kickoff"])
        if not sender(json.loads(sub_json), _digest_payload(mine, tz)):
            remove(endpoint)
        sent += 1
    return sent


def run(get_rows, interval=CHECK_EVERY, sender=send):
    """Background loop: look at today's football matches and push goals, kick-offs, full-times and reminders."""
    state, reminded = {}, set()
    last_digest_check = 0.0
    while True:
        try:
            notify_events(detect_events(state, reminded, get_rows()), sender)
        except Exception as exc:
            print(f"push: check failed: {exc}")
        if DIGEST_ROWS and time.time() - last_digest_check >= 60:
            last_digest_check = time.time()
            try:
                send_digests(DIGEST_ROWS(), sender=sender)
            except Exception as exc:
                print(f"push: digest failed: {exc}")
        time.sleep(interval)