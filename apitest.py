import time
import requests
from datetime import date, timedelta

KEY = "a2674ecb7742a44372b07d0c4bc90755"
BASE = "https://v3.football.api-sports.io"
H = {"x-apisports-key": KEY, "Connection": "close"}


def call(path, **params):
    """GET with retries: this network sometimes drops the secure connection mid-request."""
    for attempt in range(1, 7):
        try:
            r = requests.get(BASE + path, headers=H, params=params, timeout=20)
            return r.json()
        except requests.exceptions.RequestException as exc:
            print(f"  (retry {attempt}: {type(exc).__name__})")
            time.sleep(1.5)
    return {"errors": "gave up after 6 tries", "results": 0, "response": []}


day = (date.today() - timedelta(days=1)).isoformat()
d = call("/fixtures", date=day)
print("fixtures errors:", d.get("errors"), "| results:", d.get("results"))
finished = [f for f in d.get("response", []) if f["fixture"]["status"]["short"] == "FT"]
print("finished yesterday:", len(finished))

shown = 0
for f in finished[:25]:
    fid = f["fixture"]["id"]
    name = f'{f["teams"]["home"]["name"]} v {f["teams"]["away"]["name"]} ({f["league"]["name"]})'
    lu = call("/fixtures/lineups", fixture=fid)
    ev = call("/fixtures/events", fixture=fid)
    if lu.get("errors"):
        print("\nlineups error:", lu["errors"])
        break
    goals = [(e["time"]["elapsed"], e["player"]["name"], e["detail"]) for e in ev.get("response", []) if e["type"] == "Goal"]
    print(f"\n{name}\n  lineups: {lu.get('results')} teams | goals: {goals}")
    shown += 1
    if shown >= 4:
        break