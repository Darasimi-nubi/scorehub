"""Checks your LiveScore key works. Run:  python livescore_test.py
Put LIVESCORE_KEY and LIVESCORE_SECRET in your .env file first (next to app.py)."""
import json
from config import load_env
load_env()
import livescore as L

if not L.enabled():
    raise SystemExit("LIVESCORE_KEY / LIVESCORE_SECRET not found. Add both to your .env file.")

data = L._get("matches/live.json")
if data is None:
    raise SystemExit("The live scores request failed (see the message above). Check the key and secret.")
matches = data.get("match") or []
print(f"Live scores OK: {len(matches)} matches in play or just finished.")
if not matches:
    print("Nothing live right now; try again when games are on.")
    raise SystemExit
m = matches[0]
print("Sample:", m["home"]["name"], m["scores"]["score"], m["away"]["name"], "| status:", m["status"], "| time:", m.get("time"))
for part in ("events", "lineups", "statistics", "head2head"):
    url = (m.get("urls") or {}).get(part)
    if not url:
        print(f"{part}: no URL given for this match")
        continue
    d = L._get(url)
    print(f"{part}: {'OK' if d is not None else 'FAILED (your plan may not include it)'}", "" if d is None else str(json.dumps(d))[:140])