#!/usr/bin/env python3
"""Config-driven web scraper -> resumable CSV + Excel.

What this actually does (and nothing more):
  * reads sites.json - one block per site, with its own CSS selectors
  * paginates, politely (configurable delay), reusing one HTTP session
  * retries transient failures with exponential backoff (429 / 5xx / timeouts)
  * appends rows as it goes and checkpoints every page, so a crash or a
    Ctrl-C never costs more than one page of work
  * de-duplicates by an id field (item URL)
  * exports CSV always, .xlsx when openpyxl is installed

Deliberately NOT included: user-agent spoofing, proxy rotation, CAPTCHA
solving or any other bot-detection bypass. Scrape only what you are
allowed to scrape, and respect the target's robots.txt and terms.

Run with the venv python (has openpyxl):
    /home/solonext/freelance-ai/.venv/bin/python scraper.py --all
    /home/solonext/freelance-ai/.venv/bin/python scraper.py --site books --fresh
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "PortfolioDemoScraper/2.0 (educational sandbox)"
RETRY_STATUS = {429, 500, 502, 503, 504}


def load_config(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fetch(session, url, timeout, retries, backoff):
    """GET with exponential backoff. Returns response text or raises."""
    last = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code in RETRY_STATUS:
                raise requests.HTTPError("status %s" % resp.status_code)
            resp.raise_for_status()
            # requests decodes with the header charset, defaulting to
            # ISO-8859-1 when the server omits it. That turns a UTF-8 "£"
            # into mojibake ("Â£"), so a price column silently loses its
            # currency. Trust the header only when it names a real charset.
            declared = resp.encoding
            if not declared or declared.lower() in ("iso-8859-1", "latin-1", "latin"):
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as exc:                      # reported, then retried
            last = exc
            if attempt == retries:
                break
            wait = backoff * (2 ** attempt)
            print("    retry %d/%d in %.1fs (%s)" % (attempt + 1, retries, wait, exc))
            time.sleep(wait)
    raise RuntimeError("failed %s: %s" % (url, last))


def attr_text(node, name):
    """Return a tag attribute as a string.

    BeautifulSoup returns multi-valued attributes (`class`, `rel`, `headers`, ...)
    as a list, not a str. Passing that list to `re.search` raises TypeError, and
    `"x" in ["x y"]` is False even though the token is present. Always normalise
    before regexing or substring-matching an attribute value.
    """
    value = node.get(name, "") if name else ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return value or ""


def extract(item, spec, base_url):
    """Pull one field out of an element using selector + attr / regex / map.

    spec["all"] = true joins every match (e.g. tag lists) instead of taking the first.
    """
    if spec.get("all"):
        found = []
        for node in item.select(spec["selector"]):
            value = attr_text(node, spec.get("attr")) if "attr" in spec else node.get_text(" ", strip=True)
            if "regex" in spec:
                match = re.search(spec["regex"], value)
                value = match.group(1).strip() if match else ""
            if spec.get("absolute") and value:
                value = urljoin(base_url, value)
            if value:
                found.append(value)
        return ", ".join(found)

    node = item.select_one(spec["selector"])
    if node is None:
        return ""
    if "map" in spec:
        key = attr_text(node, spec.get("attr", "class"))
        for token, value in spec["map"].items():
            if token in key:
                return value
        return ""
    if "attr" in spec:
        value = attr_text(node, spec["attr"])
    else:
        value = node.get_text(" ", strip=True)
    if "regex" in spec:
        if spec.get("all"):
            found = re.findall(spec["regex"], value)
            return ", ".join(f.strip() for f in found if f.strip())
        match = re.search(spec["regex"], value)
        value = match.group(1).strip() if match else ""
    if spec.get("absolute") and value:
        value = urljoin(base_url, value)
    return value


def rows_from(html, site, page_url):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select(site["item_selector"]):
        row = {name: extract(item, spec, page_url) for name, spec in site["fields"].items()}
        if any(row.values()):
            out.append(row)
    return out


def load_state(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return {"pages_done": [], "rows": 0}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)                      # atomic: never a half-written state


def seen_ids(csv_path, id_field, fields):
    seen = set()
    if not os.path.exists(csv_path):
        return seen
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            value = row.get(id_field, "")
            if value:
                seen.add(value)
    return seen


def page_url(site, page):
    if "page_url" in site:
        return site["page_url"].format(page=page)
    return site["url"]


def scrape_site(site, args, session):
    name = site["name"]
    out_csv = os.path.join(args.out, "%s.csv" % name)
    state_path = os.path.join(args.out, "%s.state.json" % name)
    fields = list(site["fields"].keys())
    id_field = site.get("id_field", fields[0])

    if args.fresh:
        for path in (out_csv, state_path):
            if os.path.exists(path):
                os.remove(path)

    state = load_state(state_path)
    done = set(state["pages_done"])
    seen = seen_ids(out_csv, id_field, fields)
    new_file = not os.path.exists(out_csv) or os.path.getsize(out_csv) == 0
    max_pages = site.get("max_pages", 1)
    delay = site.get("delay", 0.5)
    added = skipped = 0
    accounted = 0                                       # rows already written into state["rows"]

    print("[%s] %d page(s) total, %d already done, resuming" % (name, max_pages, len(done)) if done
          else "[%s] %d page(s) to scrape" % (name, max_pages))

    with open(out_csv, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new_file:
            writer.writeheader()
        for page in range(1, max_pages + 1):
            if page in done:
                continue
            url = page_url(site, page)
            html = fetch(session, url, args.timeout, args.retries, args.backoff)
            for row in rows_from(html, site, url):
                key = row.get(id_field, "")
                if key and key in seen:
                    skipped += 1
                    continue
                writer.writerow(row)
                if key:
                    seen.add(key)
                added += 1
            fh.flush()                                   # data is safe on disk now
            done.add(page)
            state["pages_done"] = sorted(done)
            # `added` is a run-scoped cumulative counter, so it must contribute
            # only its per-page delta here. Adding the running total each page
            # double-counts every row scraped after page 1.
            state["rows"] = state.get("rows", 0) + (added - accounted)
            accounted = added
            save_state(state_path, state)
            print("    page %d/%d  rows so far: %d" % (page, max_pages, len(seen)))
            if page < max_pages:
                time.sleep(delay)                       # be polite, always

    print("[%s] done: +%d new, %d duplicate(s) skipped, %d unique -> %s"
          % (name, added, skipped, len(seen), out_csv))
    return out_csv, len(seen)


def to_excel(csv_path):
    """CSV -> styled .xlsx. Returns path, or None when openpyxl is missing."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        print("    openpyxl not installed - skipping .xlsx (CSV still written)")
        return None

    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return None

    wb = Workbook()
    ws = wb.active
    ws.title = os.path.splitext(os.path.basename(csv_path))[0][:31]
    for row in rows:
        ws.append(row)

    head = ws[1]
    fill = PatternFill("solid", fgColor="0F2A43")
    for cell in head:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for index, column in enumerate(ws.columns, start=1):
        width = max((len(str(c.value)) for c in column if c.value is not None), default=10)
        ws.column_dimensions[ws.cell(row=1, column=index).column_letter].width = min(max(width + 2, 12), 60)

    xlsx_path = os.path.splitext(csv_path)[0] + ".xlsx"
    wb.save(xlsx_path)
    print("    excel written: %s (%d data rows)" % (xlsx_path, len(rows) - 1))
    return xlsx_path


def main():
    parser = argparse.ArgumentParser(description="Config-driven, resumable web scraper.")
    parser.add_argument("--config", default=os.path.join(HERE, "sites.json"))
    parser.add_argument("--site", help="scrape only this site name")
    parser.add_argument("--all", action="store_true", help="scrape every site in the config")
    parser.add_argument("--out", default=os.path.join(HERE, "out"))
    parser.add_argument("--fresh", action="store_true", help="discard checkpoint and start over")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--backoff", type=float, default=1.5)
    args = parser.parse_args()

    sites = load_config(args.config)["sites"]
    targets = [s for s in sites if args.site in (None, s["name"])] if (args.site or args.all) else sites
    if not targets:
        sys.exit("no site matched %r (have: %s)" % (args.site, ", ".join(s["name"] for s in sites)))

    os.makedirs(args.out, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})

    for site in targets:
        csv_path, count = scrape_site(site, args, session)
        to_excel(csv_path)
        print("    %d unique rows\n" % count)


if __name__ == "__main__":
    main()
