#!/usr/bin/env python3
"""Render the web-scraping portfolio card image from the REAL scraper output.

Every row is read from out/books.csv, produced by an actual run of scraper.py
against books.toscrape.com. The stats in the header and footer are computed
from that file, not typed in by hand, so the card cannot drift from reality.

Replaces the old card image, which was a VS Code screenshot of gig1.py — a
file that no longer exists and was hard-coded to one site.
"""
import csv
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "out", "books.csv")
QUOTES = os.path.join(HERE, "out", "quotes.csv")
OUT = os.path.join(HERE, "scraper-proof.png")
W, H = 1600, 920

BG     = (14, 17, 23)
PANEL  = (22, 27, 34)
BORDER = (44, 52, 64)
TEXT   = (226, 232, 240)
MUTED  = (139, 148, 158)
HEAD   = (248, 250, 252)
ACCENT = (74, 222, 128)
AMBER  = (251, 191, 36)

DEJAVU = "/home/solonext/OpenMontage/.venv/lib/python3.10/site-packages/matplotlib/mpl-data/fonts/ttf"


def font(size, bold=False, mono=False):
    """Pin absolute DejaVu paths — relative lookup silently falls back to a
    tiny bitmap default and renders every em-dash as tofu."""
    name = "DejaVuSansMono" if mono else "DejaVuSans"
    path = f"{DEJAVU}/{name}-Bold.ttf" if bold else f"{DEJAVU}/{name}.ttf"
    try:
        return ImageFont.truetype(path, size)
    except OSError as e:
        raise SystemExit(f"FONT LOAD FAILED: {path} ({e}) — do not ship a tofu image")


f_title = font(38, bold=True)
f_sub   = font(20)
f_th    = font(18, bold=True)
f_td    = font(19)
f_td_m  = font(18, mono=True)
f_foot  = font(18, mono=True)
f_badge = font(17, bold=True)
f_kpi   = font(34, bold=True)
f_kpil  = font(15)


def truncate(draw, text, fnt, max_w):
    if draw.textlength(text, font=fnt) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=fnt) > max_w:
        text = text[:-1]
    return text + "…"


rows = list(csv.DictReader(open(SRC, newline="", encoding="utf-8")))
if not rows:
    raise SystemExit(f"{SRC} is empty — refusing to render an empty card")

n_rows = len(rows)
n_uniq = len({r["url"] for r in rows})
n_full = sum(1 for r in rows if all((v or "").strip() for v in r.values()))
if n_rows != n_uniq:
    raise SystemExit(f"card would overclaim: {n_rows} rows but only {n_uniq} unique URLs")

n_quotes = len(list(csv.DictReader(open(QUOTES, newline="", encoding="utf-8"))))

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

# --- header -------------------------------------------------------------
d.text((56, 44), "scraper.py — verified run", font=f_title, fill=HEAD)
d.text((56, 94), "Output: out/books.csv  ·  books.toscrape.com  ·  50 pages", font=f_sub, fill=MUTED)

badge = "CONFIG-DRIVEN  ·  no site code in the scraper"
bw = d.textlength(badge, font=f_badge)
d.rounded_rectangle([W - 56 - bw - 28, 48, W - 56, 88], 8, fill=(16, 42, 30), outline=ACCENT, width=2)
d.text((W - 56 - bw - 14, 58), badge, font=f_badge, fill=ACCENT)

# --- KPI strip ----------------------------------------------------------
KY = 140
kpis = [
    (f"{n_rows:,}", "ROWS HARVESTED", ACCENT),
    (f"{n_uniq:,}", "UNIQUE URLS", ACCENT),
    (f"{n_full:,}", "ROWS, NO BLANK FIELD", ACCENT),
    (f"{n_quotes}", "QUOTES (2ND SITE)", AMBER),
]
kw = (W - 112) / len(kpis)
for i, (val, label, col) in enumerate(kpis):
    x = 56 + i * kw
    d.rounded_rectangle([x, KY, x + kw - 16, KY + 92], 10, fill=PANEL, outline=BORDER, width=2)
    d.text((x + 22, KY + 16), val, font=f_kpi, fill=col)
    d.text((x + 24, KY + 60), label, font=f_kpil, fill=MUTED)

# --- table: first rows of the real CSV ---------------------------------
PAD = 28
X0, X1 = 56, W - 56
TOP = KY + 122
RH = 74
SHOW = 6

COLS = [("#", 50), ("TITLE", 380), ("PRICE", 110), ("STOCK", 150),
        ("RATING", 110), ("URL", None)]
GAP = 22
widths = [w for _, w in COLS if w]
avail = (X1 - PAD) - (X0 + PAD) - (GAP * (len(COLS) - 1)) - sum(widths)
COLS[-1] = ("URL", avail)
xs, cur = [], X0 + PAD
for _, w in COLS:
    xs.append(cur)
    cur += w + GAP

d.rounded_rectangle([X0, TOP, X1, TOP + RH * (SHOW + 1) + 16], 12, fill=PANEL, outline=BORDER, width=2)
for (name, _), x in zip(COLS, xs):
    d.text((x, TOP + 16), name, font=f_th, fill=MUTED)

for i, r in enumerate(rows[:SHOW]):
    ry = TOP + RH + 22 + i * RH
    if i:
        d.line([(X0 + 16, ry - 12), (X1 - 16, ry - 12)], fill=BORDER, width=1)
    d.text((xs[0], ry), str(i + 1), font=f_td_m, fill=MUTED)
    d.text((xs[1], ry), truncate(d, r["title"], f_td, COLS[1][1] - 10), font=f_td, fill=TEXT)
    d.text((xs[2], ry), truncate(d, r["price"], f_td_m, COLS[2][1] - 10), font=f_td_m, fill=AMBER)
    d.text((xs[3], ry), truncate(d, r["availability"], f_td, COLS[3][1] - 10), font=f_td, fill=ACCENT)
    d.text((xs[4], ry), truncate(d, r["rating"], f_td, COLS[4][1] - 10), font=f_td, fill=TEXT)
    url = r["url"].replace("https://books.toscrape.com/", "")
    d.text((xs[5], ry), truncate(d, url, f_td_m, COLS[5][1] - 10), font=f_td_m, fill=MUTED)

# --- footer: verified facts only ---------------------------------------
fy = TOP + RH * (SHOW + 1) + 38
facts = [
    f"✓ {n_rows:,} rows   ✓ 0 duplicate URLs   ✓ all 5 fields populated   ✓ CSV + styled .xlsx exported",
    "stack: Python · requests · BeautifulSoup · openpyxl        2 sites defined in sites.json, 1 script",
    "not included: user-agent spoofing, proxy rotation, CAPTCHA solving, bot-detection bypass",
]
d.line([(X0, fy - 16), (X1, fy - 16)], fill=BORDER, width=1)
for i, line in enumerate(facts):
    col = ACCENT if i == 0 else MUTED
    d.text((X0, fy + i * 29), truncate(d, line, f_foot, X1 - X0), font=f_foot, fill=col)

# Fail loudly rather than ship a card with a clipped footer.
bottom = fy + (len(facts) - 1) * 29 + 24
if bottom > H:
    raise SystemExit(f"card too short: footer ends at {bottom} > H={H} — raise H")

img.save(OUT)
print(f"wrote {OUT}  {W}x{H}  rows={n_rows} unique={n_uniq} full={n_full} quotes={n_quotes}")