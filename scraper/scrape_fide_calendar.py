# One-time ICS builder for FIDE event pages (JS-rendered).
# Outputs: docs/fide_events.ics
#
# Run in GitHub Actions (manual) or locally:
#   pip install -r scraper/requirements.txt
#   python -m playwright install --with-deps chromium
#   python scraper/scrape_fide_calendar.py

import asyncio, os, re, json, hashlib
from datetime import datetime
from dateutil import parser as dtp
from ics import Calendar, Event
from playwright.async_api import async_playwright

# --- Your URLs (prefilled) ---
URLS = [
    "https://calendar.fide.com/calendar.php?id=3079",
    "https://calendar.fide.com/calendar.php?id=3220",
    "https://calendar.fide.com/calendar.php?id=6573",
    "https://calendar.fide.com/calendar.php?id=3166",
    "https://calendar.fide.com/calendar.php?id=4750",
    "https://calendar.fide.com/calendar.php?id=4268",
    "https://calendar.fide.com/calendar.php?id=3293",
    "https://calendar.fide.com/calendar.php?id=6475",
    "https://calendar.fide.com/calendar.php?id=5767",
    "https://calendar.fide.com/calendar.php?id=4007",
    "https://calendar.fide.com/calendar.php?id=5775",
    "https://calendar.fide.com/calendar.php?id=2790",
    "https://calendar.fide.com/calendar.php?id=3613",
]

OUT_DIR = "docs"
ICS_PATH = os.path.join(OUT_DIR, "fide_events.ics")

def first_nonempty(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def parse_maybe_dt(s):
    if not s:
        return None
    try:
        return dtp.parse(s)
    except Exception:
        return None

async def scrape_event_from_page(page, url):
    """Return a dict with: title, start_dt, end_dt, location, description, url"""
    await page.goto(url, wait_until="networkidle")

    # Collect meta + JSON-LD
    meta = await page.evaluate("""
        () => {
          const out = { metas: {}, ld: [], og: {} };
          for (const m of document.querySelectorAll('meta[name], meta[property], meta[itemprop]')) {
            const key = m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('itemprop');
            const val = m.getAttribute('content') || '';
            if (key) out.metas[key] = val;
            if (key && key.startsWith('og:')) out.og[key] = val;
          }
          for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
            let t = s.textContent || "";
            try { out.ld.push(JSON.parse(t)); } catch(e){}
          }
          return out;
        }
    """)

    # Basic DOM extraction
    dom = await page.evaluate("""
        () => {
          const out = {};
          out.title = (document.querySelector('h1')?.innerText
                    || document.title || '').trim();
          out.location = (document.querySelector('[itemprop=location]')?.innerText
                       || document.querySelector('.location, .venue')?.innerText
                       || '').trim();
          const times = Array.from(document.querySelectorAll('time'));
          out.times = times.map(t => ({
            text: (t.innerText || '').trim(),
            datetime: (t.getAttribute('datetime') || '').trim()
          }));
          const main = document.querySelector('main') || document.querySelector('#main') || document.body;
          out.fullText = (main?.innerText || '').replace(/\\s+\\n/g, '\\n').trim();
          return out;
        }
    """)

    title = first_nonempty(
        dom.get("title"),
        meta["og"].get("og:title") if meta else None,
        meta["metas"].get("title") if meta else None
    ) or "FIDE Event"

    # Dates from <time>
    start_dt = None
    end_dt = None
    for t in (dom.get("times") or []):
        dt_guess = parse_maybe_dt(t.get("datetime") or t.get("text"))
        if dt_guess and not start_dt:
            start_dt = dt_guess
        elif dt_guess and start_dt and not end_dt and dt_guess > start_dt:
            end_dt = dt_guess

    # Dates from JSON-LD
    if not start_dt and meta and meta.get("ld"):
        for blob in meta["ld"]:
            try:
                candidates = blob if isinstance(blob, list) else [blob]
                for item in candidates:
                    if isinstance(item, dict) and str(item.get("@type","")).lower().endswith("event"):
                        s = item.get("startDate")
                        e = item.get("endDate")
                        if not start_dt:
                            start_dt = parse_maybe_dt(s)
                        if not end_dt:
                            end_dt = parse_maybe_dt(e)
            except Exception:
                pass

    # Meta fallbacks
    if not start_dt:
        start_dt = parse_maybe_dt(first_nonempty(
            meta["metas"].get("event:start_time") if meta else None,
            meta["metas"].get("startDate") if meta else None,
            meta["metas"].get("date") if meta else None,
            meta["og"].get("og:startDate") if meta else None
        ))
    if not end_dt:
        end_dt = parse_maybe_dt(first_nonempty(
            meta["metas"].get("event:end_time") if meta else None,
            meta["metas"].get("endDate") if meta else None,
            meta["og"].get("og:endDate") if meta else None
        ))

    location = first_nonempty(
        dom.get("location"),
        meta["metas"].get("event:location") if meta else None,
        meta["og"].get("og:site_name") if meta else None
    ) or ""

    # Description: always include source URL + full page text (best effort)
    full_text = dom.get("fullText") or ""
    if len(full_text) > 50000:
        full_text = full_text[:50000] + "\n\n[Truncated]"
    description = f"Source: {url}\n\n{full_text}".strip()

    return {
        "title": title,
        "start_dt": start_dt,   # may be None → placeholder begin
        "end_dt": end_dt,
        "location": location,
        "description": description,
        "url": url
    }

def event_uid(url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"{h[:32]}@fid-calendar-scrape"

def write_ics(records):
    os.makedirs(OUT_DIR, exist_ok=True)
    cal = Calendar()
    for r in records:
        ev = Event()
        ev.uid = event_uid(r["url"])
        ev.name = r["title"]
        ev.location = r["location"]
        ev.description = r["description"]
        if r["start_dt"]:
            ev.begin = r["start_dt"]
        else:
            ev.begin = datetime.now()  # placeholder so it still imports
        if r["end_dt"]:
            ev.end = r["end_dt"]
        cal.events.add(ev)
    with open(ICS_PATH, "w", encoding="utf-8") as f:
        f.writelines(cal)
    print(f"Wrote ICS with {len(cal.events)} events → {ICS_PATH}")

async def main():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        for url in URLS:
            results.append(await scrape_event_from_page(page, url))
        await browser.close()
    write_ics(results)

if __name__ == "__main__":
    asyncio.run(main())
