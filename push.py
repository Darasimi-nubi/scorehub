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
import time
from urllib.parse import urlparse

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
    return con


def valid_endpoint(url):
    try:
        u = urlparse(url)
    except Exception:
        return False
    host = u.hostname or ""
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def save(sub, matches):
    """Store (or refresh) one browser's subscription and the match ids it wants goals for."""
    ids = sorted({int(x) for x in matches if str(x).isdigit()})[:300]
    with _lock, _db() as con:
        con.execute("INSERT INTO subs (endpoint, sub, matches, updated) VALUES (?,?,?,?)"
                    " ON CONFLICT(endpoint) DO UPDATE SET sub=excluded.sub, matches=excluded.matches,"
                    " updated=excluded.updated", (sub["endpoint"], json.dumps(sub), json.dumps(ids), time.time()))


def remove(endpoint):
    with _lock, _db() as con:
        con.execute("DELETE FROM subs WHERE endpoint = ?", (endpoint,))


def subscribers_for(match_id):
    with _lock, _db() as con:
        rows = con.execute("SELECT sub, matches FROM subs").fetchall()
    return [json.loads(s) for s, m in rows if match_id in json.loads(m)]


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


def detect_goals(state, rows):
    """Compare live rows with the scores seen last time; returns a list of (row, scoring_side).

    A match seen for the first time only sets a baseline, so a restart never replays old goals.
    A score going down (a goal ruled out) quietly lowers the baseline.
    """
    goals = []
    for row in rows:
        now = _score(row)
        before = state.get(row["id"])
        state[row["id"]] = now
        if before is None:
            continue
        for i, side in enumerate(("home", "away")):
            if now[i] > before[i]:
                goals.append((row, side))
    return goals


def goal_payload(row, side):
    hs, as_ = _score(row)
    team = row["home"] if side == "home" else row["away"]
    minute = f" · {row['clock']}" if row.get("clock") else ""
    return {"title": f"⚽ GOAL! {row['home']} {hs}–{as_} {row['away']}",
            "body": f"{team} scored{minute}", "url": f"/match/{row['id']}", "tag": f"goal-{row['id']}"}


def notify(goals, sender=send):
    for row, side in goals:
        payload = goal_payload(row, side)
        for sub in subscribers_for(row["id"]):
            if not sender(sub, payload):
                remove(sub["endpoint"])


def run(get_live_rows, interval=CHECK_EVERY, sender=send):
    """Background loop: look at the live matches, push every new goal."""
    state = {}
    while True:
        try:
            notify(detect_goals(state, get_live_rows()), sender)
        except Exception as exc:
            print(f"push: goal check failed: {exc}")
        time.sleep(interval)
