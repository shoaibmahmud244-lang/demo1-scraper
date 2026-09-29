# Demo 1 — web scraper (Gig #1)

A config-driven Python scraper that turns a paginated website into a clean,
de-duplicated CSV and a formatted `.xlsx`.

Everything claimed on the gig listing is produced by this code. The run log
below is a real execution, not an illustration.

## Files

| File | Purpose |
|---|---|
| `scraper.py` | The scraper. No site-specific code lives here. |
| `sites.json` | Per-site config: URL, page count, delay, CSS selectors, field rules. |
| `out/books.csv`, `out/books.xlsx` | Output from the last run (books.toscrape.com). |
| `out/quotes.csv`, `out/quotes.xlsx` | Output from the second site in the same config. |
| `out/*.state.json` | Checkpoints — delete to force a full re-crawl. |
| `make_card.py` | Renders `scraper-proof.png` (the portfolio card) from `out/books.csv`. |
| `scraper-proof.png` | The card image. Every number on it is computed from the CSV. |

## Run it

Needs `requests` and `beautifulsoup4`; `openpyxl` is optional and enables the
Excel export.

```bash
/home/solonext/freelance-ai/.venv/bin/python scraper.py --all          # all sites
/home/solonext/freelance-ai/.venv/bin/python scraper.py --site books   # one site
/home/solonext/freelance-ai/.venv/bin/python scraper.py --all --fresh  # ignore checkpoint
```

## Adding a site

Add a block to `sites.json`. No Python changes needed.

```json
{
  "name": "example",
  "page_url": "https://example.com/page-{page}.html",
  "max_pages": 20,
  "delay": 0.5,
  "id_field": "url",
  "item_selector": "div.product",
  "fields": {
    "title": { "selector": "h2 a", "attr": "title" },
    "price": { "selector": "span.cost", "regex": "([0-9]+\\.[0-9]{2})" },
    "url":   { "selector": "h2 a", "attr": "href", "absolute": true }
  }
}
```

Field options: `attr` (pull an attribute), `regex` (clean the text with a
capture group), `map` (map a class name to a value, e.g. star ratings),
`absolute` (resolve a relative href), `all` (join every match).

## What it actually does

- Reuses one HTTP session, with a descriptive User-Agent
- Retries timeouts and 429/5xx with exponential backoff, then fails loudly
- Polite, configurable delay between pages
- Appends rows as it goes and writes a checkpoint after every page, so an
  interruption costs at most one page
- De-duplicates by the configured id field
- Exports CSV always, styled `.xlsx` when `openpyxl` is present

## Verified run

Both sites are public practice sandboxes built for scraping practice.

```
books.toscrape.com    50 pages → 1,000 unique rows, 0 duplicate URLs,
                      all 5 fields populated on every row, prices keep
                      their currency symbol (£)
quotes.toscrape.com   1 page   → 10 rows with text, author, author URL, tags
```

Re-run 2026-09-29 after fixing the currency bug below; the numbers above are
from that run, not the earlier one.

Two behaviours were tested rather than assumed:

- **Resume.** The `books` checkpoint was rolled back to page 10, then the run
  was repeated. It resumed at page 11, finished at page 50, and the file still
  held exactly 1,000 unique rows — no duplicates, no double-counting.
- **Retries.** A deliberately bad URL was fetched with 2 retries: it waited
  0.1s, then 0.2s, then raised a clean error instead of a stack trace.

## Bug found and fixed 2026-09-29

The `price` column was silently losing its currency symbol — output was `51.77`
instead of `£51.77`. Two independent causes:

1. `resp.text` decodes using the `Content-Type` charset. `books.toscrape.com`
   omits it, so `requests` falls back to ISO-8859-1 and turns a UTF-8 `£` into
   mojibake (`Â£`). `fetch()` now trusts the header only when it names a real
   charset, and otherwise falls back to `apparent_encoding`.
2. The `sites.json` regex `([0-9]+\.[0-9]{2})` could not match a currency
   prefix even once decoding was correct. It is now `([£$€]?[0-9]+[.,][0-9]{2})`.

Both sites were re-crawled after the fix; all 1,000 prices carry `£`.

## Four more bugs, found by running it instead of reading it

The currency bug above was found because the numbers looked wrong. These four
were found by executing the failure paths directly. None of them show up in a
happy-path demo, and all four are the kind of thing that only appears under
resume, retry, or operator error — which is to say, in production.

**1. `sys.exit()` used without importing `sys`** — `NameError: name 'sys' is not
defined`. The scraper's retry logic handled every network failure and none of
the operator failures. Passing a bad `--site` produced a traceback instead of a
usable message. Now exits 1 with a message listing the valid site names.

**2. The resume counter double-counted rows.** `state["rows"] = state.get("rows", 0) + added`
used a *run-scoped cumulative* `added` inside a per-page loop, so every page
after the first re-added the entire running total. Thirty real rows reported as
sixty — before any crash occurred. It now contributes only its per-page delta.

**3. Multi-valued attributes crashed the parser.** `node.get("class", "")`
returns a *list* from BeautifulSoup, not a string, so `re.search` raised
`TypeError: expected string or bytes-like object`. The same value also broke
substring lookup: `"price" in ["price", "red"]` is `False` even though the token
is present. `attr_text()` now normalises list and tuple values to a string before
any regex or substring work.

**4. A re-run destroyed its own evidence.** `demo3-workflow/automate.py` opened
its run log with mode `"w"`, so a routine second run overwrote the log of the
first. The second run also legitimately re-validated every contact as a
duplicate, so the replacement log claimed `Upserted 0 contacts` while the CRM file
still held three — a fabricated record. It now appends behind a separator, and
`os` was imported while fixing it.

All four were re-verified by execution after the fix, and both sites re-crawled
clean: 1,000 and 10 rows.

## Deliberately not included

No user-agent spoofing, no proxy rotation, no CAPTCHA solving, no
bot-detection bypass. That work breaks the target's terms and puts the buyer's
domain at risk. Sites needing JavaScript rendering are declined after testing,
and scraping a site that forbids it is declined outright.
