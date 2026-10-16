"""Draws the 1200x630 picture shown when a match link is shared on WhatsApp, X, Telegram and so on."""
import io
import os
import time
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts", "Inter-Bold.otf")
BG, PANEL, TEXT, MUTED, GOLD, LIVE = "#0d0d12", "#161a22", "#f3f3f5", "#8d93a0", "#f5b335", "#31c27c"
WAT = timezone(timedelta(hours=1))  # Nigeria time

_fonts = {}
_cache = {}  # key -> (made_at, png bytes)


def font(size):
    if size not in _fonts:
        try:
            _fonts[size] = ImageFont.truetype(FONT, size)
        except OSError:
            _fonts[size] = ImageFont.load_default(size)  # still works if the font file is missing
    return _fonts[size]


def _initials(name):
    words = [w for w in name.replace("-", " ").split() if w[0].isalnum()]
    if not words:
        return "?"
    return words[0][:3].upper() if len(words) == 1 else "".join(w[0] for w in words[:3]).upper()


def _hue_colour(name):
    h = sum(ord(c) * (i + 1) for i, c in enumerate(name)) % 360
    img = Image.new("HSV", (1, 1), (int(h / 360 * 255), 150, 150)).convert("RGB")
    return img.getpixel((0, 0))


def _fit(draw, text, max_w, start, minimum=26):
    size = start
    while size > minimum and draw.textlength(text, font=font(size)) > max_w:
        size -= 2
    return font(size)


def _wrap_name(draw, name, max_w):
    """Team name on one line if it fits, otherwise two lines."""
    f = _fit(draw, name, max_w, 44, 34)
    if draw.textlength(name, font=f) <= max_w:
        return [name], f
    words = name.split()
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        w = max(draw.textlength(a, font=font(36)), draw.textlength(b, font=font(36)))
        if best is None or w < best[0]:
            best = (w, a, b)
    if best:
        return [best[1], best[2]], _fit(draw, max([best[1], best[2]], key=len), max_w, 38, 26)
    return [name], f


def status_text(m):
    st = m["status"]
    if st == "finished":
        return "FULL TIME"
    if st == "live":
        if m.get("period") == "HT":
            return "HALF TIME"
        return f"LIVE  {m['clock']}" if m.get("clock") else "LIVE"
    if st == "postponed":
        return "POSTPONED"
    ko = datetime.fromisoformat(m["kickoff"])
    ko = (ko if ko.tzinfo else ko.replace(tzinfo=timezone.utc)).astimezone(WAT)
    return f"{ko.strftime('%a')} {ko.day} {ko.strftime('%b')} · {ko.strftime('%H:%M')} WAT"


def score_text(m):
    if m["status"] in ("scheduled", "postponed") or m.get("home_score") is None or m.get("away_score") is None:
        return "vs"
    return f"{m['home_score']} – {m['away_score']}"


def _badge(draw, cx, cy, name):
    r = 78
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_hue_colour(name))
    text = _initials(name)
    f = font(58 if len(text) < 3 else 46)
    draw.text((cx, cy), text, font=f, fill="white", anchor="mm")


def render(m, site="scorehubber.name.ng"):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, W - 40, H - 40), radius=28, fill=PANEL)

    # competition and status
    league = m["league"] + (f"  ·  {m['country']}" if m.get("country") else "")
    d.text((80, 92), league, font=_fit(d, league, 700, 32, 22), fill=GOLD, anchor="lm")
    stat = status_text(m)
    sf = font(30)
    sw = d.textlength(stat, font=sf)
    colour = LIVE if m["status"] == "live" else MUTED
    d.rounded_rectangle((W - 80 - sw - 40, 62, W - 80, 122), radius=30, outline=colour, width=3)
    d.text((W - 80 - sw / 2 - 20, 92), stat, font=sf, fill=colour, anchor="mm")

    # teams and score
    for cx, name in ((260, m["home"]), (W - 260, m["away"])):
        _badge(d, cx, 270, name)
        lines, nf = _wrap_name(d, name, 340)
        y = 390 if len(lines) == 1 else 372
        for line in lines:
            d.text((cx, y), line, font=nf, fill=TEXT, anchor="mm")
            y += nf.size + 8
    score = score_text(m)
    d.text((W / 2, 262), score, font=_fit(d, score, 380, 150, 60), fill=TEXT, anchor="mm")

    # footer
    d.ellipse((80, 512, 104, 536), fill=GOLD)
    d.text((118, 524), "ScoreHub", font=font(38), fill=TEXT, anchor="lm")
    d.text((W - 80, 524), site, font=font(30), fill=MUTED, anchor="rm")

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def png_for(m, site):
    """PNG for a match, remembered for a minute (live) or an hour (otherwise) so crawlers don't redraw it."""
    key = (m["id"], m["status"], m.get("home_score"), m.get("away_score"), m.get("clock"), m.get("period"), m["kickoff"])
    hit = _cache.get(key)
    ttl = 60 if m["status"] == "live" else 3600
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    data = render(m, site)
    if len(_cache) > 300:
        _cache.clear()
    _cache[key] = (time.time(), data)
    return data


def site_card(site="scorehubber.name.ng"):
    """Generic picture for pages that are not a single match."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, W - 40, H - 40), radius=28, fill=PANEL)
    d.ellipse((W / 2 - 150, 150, W / 2 - 110, 190), fill=GOLD)
    d.text((W / 2 - 90, 172), "ScoreHub", font=font(110), fill=TEXT, anchor="lm")
    d.text((W / 2, 330), "Live scores, results and tables", font=font(46), fill=TEXT, anchor="mm")
    d.text((W / 2, 400), "Football · Basketball · Hockey · Tennis · Cricket", font=font(32), fill=MUTED, anchor="mm")
    d.text((W / 2, 520), site, font=font(34), fill=GOLD, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()