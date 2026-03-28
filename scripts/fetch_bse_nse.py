#!/usr/bin/env python3
"""
Fetch BSE & NSE corporate announcements for GitHub Pages.
Uses the official `bse` Python package which handles session cookies,
rate limiting, and correct field names automatically.

Install: pip install bse requests
"""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── OUTPUT PATH ─────────────────────────────────────────────────────────────
OUT = Path(__file__).parent.parent / "data" / "bse_nse.json"
OUT.parent.mkdir(exist_ok=True)

items = []
seen  = set()
log   = []

def slug(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())[:60]

def ms_from_dt(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def age_str(ts_ms: int) -> str:
    secs = (datetime.now(timezone.utc).timestamp() * 1000 - ts_ms) / 1000
    if secs < 0:      return "just now"
    if secs < 60:     return "just now"
    if secs < 3600:   return f"{int(secs/60)}m ago"
    if secs < 86400:  return f"{int(secs/3600)}h ago"
    return f"{int(secs/86400)}d ago"

def parse_dt(s: str) -> datetime | None:
    """Try multiple date formats; return UTC datetime or None."""
    if not s:
        return None
    s = s.strip()
    # BSE format: "28 Mar 2026 14:35:22"  or  "2026-03-28T14:35:22"
    fmts = [
        '%d %b %Y %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
        '%d/%m/%Y %H:%M:%S',
        '%d %b %Y',
        '%Y-%m-%d',
    ]
    for fmt in fmts:
        try:
            clean = re.sub(r'\s+\+\d{4}$', '', s).strip()
            dt = datetime.strptime(clean, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    return None

def classify(title: str) -> str:
    t = (title or '').lower()
    if re.search(r'dividend|buyback|bonus|rights issue|stock split|face value', t): return 'dividend'
    if re.search(r'\bq[1-4]\b|quarter|result|profit|revenue|earnings|pat\b|ebitda|financial result', t): return 'results'
    if re.search(r'board meeting|agm|egm|annual general|extraordinary general|board of directors', t): return 'board'
    if re.search(r'insider|promoter|stake|pledge|bulk deal|block deal', t): return 'insider'
    return 'filing'

def add(title: str, link: str, source: str, dt: datetime | None, ann_type: str | None = None):
    title = (title or '').strip()
    if not title or len(title) < 5:
        return
    k = slug(title)
    if k in seen:
        return
    seen.add(k)
    ts  = ms_from_dt(dt) if dt else 0
    age = age_str(ts)    if ts else ''
    items.append({
        "title":  title,
        "link":   link or "#",
        "source": source,
        "ts":     ts,
        "age":    age,
        "type":   ann_type or classify(title),
    })

def strip_html(s: str) -> str:
    return re.sub(r'<[^>]+>', '', s or '').strip()

# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 1: BSE via official `bse` Python package
# This package handles: cookie/session init, Referer headers, rate limiting
# Field mapping (confirmed from BSE Angular template & package source):
#   HEADLINE     = announcement title
#   NEWS_DT      = datetime string "28 Mar 2026 14:35:22"
#   SCRIP_CD     = BSE scrip code
#   SCRIP_NAME   = company name  (sometimes ShortName)
#   CATEGORYNAME = "Corp. Action" | "Result" | "Board Meeting" | "AGM/EGM" |
#                  "Insider Trading" | "Updates" | "New Listing" | "Others"
#   ATTACHMENTNAME = PDF filename for direct link
# ─────────────────────────────────────────────────────────────────────────────
def fetch_bse_official():
    try:
        from bse import BSE
        from bse.constants import CATEGORY

        print("BSE: initialising session...")
        with BSE(download_folder='/tmp/bse') as bse:

            # Fetch today's announcements — all categories
            data = bse.announcements(page_no=1)
            rows = data.get('Table') or []
            print(f"BSE announcements: {len(rows)} rows on page 1")

            for row in rows:
                title  = (row.get('HEADLINE') or '').strip()
                scrip  = (row.get('SCRIP_NAME') or row.get('ShortName') or '').strip()
                dt_str = row.get('NEWS_DT') or row.get('DissemDT') or ''
                cat    = (row.get('CATEGORYNAME') or '').lower()
                attch  = row.get('ATTACHMENTNAME') or ''
                code   = row.get('SCRIP_CD') or ''

                link = (
                    f'https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attch}'
                    if attch else
                    f'https://www.bseindia.com/corporates/ann.html?scripcode={code}'
                )

                # Map BSE category names to our types
                tp = ('dividend' if re.search(r'corp.*action|dividend|buyback|bonus|rights|split', cat)
                      else 'results'  if re.search(r'result|financial', cat)
                      else 'board'    if re.search(r'board|agm|egm', cat)
                      else 'insider'  if re.search(r'insider', cat)
                      else classify(title))

                full = f"{scrip}: {title}" if scrip and scrip.lower() not in title.lower() else title
                add(full, link, 'BSE', parse_dt(dt_str), tp)

            # Also fetch forthcoming corporate actions (dividends, bonuses etc)
            today = datetime.now(timezone.utc)
            actions = bse.actions(
                from_date=today - timedelta(days=2),
                to_date=today + timedelta(days=7)
            )
            print(f"BSE actions: {len(actions)} items")
            for a in actions:
                scrip   = (a.get('scrip_name') or a.get('SCRIP_NAME') or '').strip()
                purpose = (a.get('purpose') or a.get('PURPOSE') or '').strip()
                ex_date = a.get('ex_date') or a.get('EX_DATE') or ''
                rec_date= a.get('record_date') or ''
                code    = a.get('scrip_code') or a.get('SCRIP_CD') or ''
                link    = f'https://www.bseindia.com/corporates/ann.html?scripcode={code}'

                if not scrip or not purpose:
                    continue
                title   = f"{scrip}: {purpose}"
                if ex_date:
                    title += f" (Ex: {ex_date})"
                dt = parse_dt(ex_date) or parse_dt(rec_date)
                add(title, link, 'BSE Actions', dt, 'dividend')

            # Result calendar — next 7 days
            results = bse.resultCalendar(
                from_date=today,
                to_date=today + timedelta(days=7)
            )
            print(f"BSE result calendar: {len(results)} items")
            for r in results:
                scrip   = (r.get('scrip_name') or r.get('SCRIP_NAME') or '').strip()
                res_dt  = r.get('result_date') or r.get('RESULT_DATE') or ''
                code    = r.get('scrip_code') or r.get('SCRIP_CD') or ''
                link    = f'https://www.bseindia.com/corporates/ann.html?scripcode={code}'
                if scrip:
                    add(f"{scrip}: Results expected", link, 'BSE Calendar',
                        parse_dt(res_dt), 'results')

        log.append("BSE official: OK")

    except ImportError:
        log.append("BSE official: bse package not installed")
        print("WARNING: `bse` package not available — run: pip install bse")
    except Exception as e:
        log.append(f"BSE official: FAILED — {e}")
        print(f"BSE official ERROR: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 2: NSE via nsepython package
# Handles NSE session cookies which are mandatory
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nse_official():
    try:
        import nsepython as nse  # type: ignore

        print("NSE: fetching corporate announcements...")
        # nsepython uses requests with NSE cookies handled automatically
        data = nse.nse_get_corporate_announcements()  # type: ignore

        if isinstance(data, list):
            print(f"NSE announcements: {len(data)} items")
            for item in data[:80]:
                desc    = (item.get('desc') or item.get('subject') or '').strip()
                symbol  = (item.get('symbol') or '').strip()
                dt_str  = item.get('bDt') or item.get('an_dt') or ''
                attch   = item.get('attchmntFile') or ''
                link    = (f'https://nsearchives.nseindia.com/corporate/{attch}'
                           if attch else
                           f'https://www.nseindia.com/companies-listing/corporate-filings-announcements')
                title   = f"{symbol}: {desc}" if symbol else desc
                add(title, link, 'NSE', parse_dt(dt_str))
        log.append("NSE official: OK")

    except ImportError:
        log.append("NSE official: nsepython not installed")
        print("WARNING: nsepython not available — run: pip install nsepython")
    except Exception as e:
        log.append(f"NSE official: FAILED — {e}")
        print(f"NSE official ERROR: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 3: RSS feeds — work server-side (no CORS), multiple sources
# ─────────────────────────────────────────────────────────────────────────────
def fetch_rss(url: str, source: str, default_type: str | None = None):
    import requests
    try:
        r = requests.get(url, timeout=12, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; NewsBot/1.0)',
        })
        r.raise_for_status()
        root = ET.fromstring(r.content)
        count = 0
        for item in root.iter('item'):
            title = strip_html((item.findtext('title') or '').strip())
            link  = (item.findtext('link') or '').strip()
            dt_s  = item.findtext('pubDate') or item.findtext('{http://purl.org/dc/elements/1.1/}date') or ''
            if title:
                add(title, link, source, parse_dt(dt_s), default_type)
                count += 1
        log.append(f"{source}: {count} items")
        print(f"{source}: {count} items")
    except Exception as e:
        log.append(f"{source}: FAILED — {e}")
        print(f"{source} ERROR: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 4: Google News RSS — NSE/BSE queries (works server-side)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_gnews(query: str, source: str, default_type: str | None = None):
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    fetch_rss(url, source, default_type)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("BSE/NSE Data Fetcher")
print("=" * 60)

# Primary: official packages (correct fields, proper session handling)
fetch_bse_official()
fetch_nse_official()

# Secondary: RSS feeds (always run — add depth and recency)
fetch_rss(
    "https://www.moneycontrol.com/rss/corporateannouncements.xml",
    "Moneycontrol"
)
fetch_rss(
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "ET Markets"
)
fetch_rss(
    "https://www.livemint.com/rss/companies",
    "LiveMint"
)
fetch_rss(
    "https://www.business-standard.com/rss/markets-106.rss",
    "Business Standard"
)

# Google News — targeted BSE/NSE queries
fetch_gnews("NSE+BSE+board+meeting+results+dividend+India+corporate+2026", "Exchange News")
fetch_gnews("BSE+NSE+quarterly+results+earnings+India+2026", "BSE/NSE Results", "results")
fetch_gnews("NSE+BSE+dividend+bonus+buyback+India+2026", "Corp Actions", "dividend")
fetch_gnews("NSE+BSE+board+meeting+AGM+EGM+India+2026", "Board Meetings", "board")
fetch_gnews("NSE+bulk+deal+block+deal+promoter+buying+India", "Bulk/Block Deals", "insider")

# Sort newest first
items.sort(key=lambda x: x.get('ts', 0), reverse=True)

# Trim to 200 most recent
items_out = items[:200]

output = {
    "updated": datetime.now(timezone.utc).isoformat(),
    "count":   len(items_out),
    "log":     log,
    "items":   items_out,
}

OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2))
print("=" * 60)
print(f"Saved {len(items_out)} items → {OUT}")
print(f"Log: {log}")
print("=" * 60)
