# One-time ICS builder for FIDE event pages (JS-rendered).
# Outputs: docs/fide_events.ics

import asyncio, os, re, json, hashlib
from datetime import datetime, date, timedelta
from dateutil import parser as dtp
from ics import Calendar, Event
from playwright.async_api import async_playwright

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

MONTHS = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|June|July|August|September|October|November|December)"

def clean_text(s: str) -> str:
    s = (s or "").replace("\r", "")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def first_nonempty(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def parse_maybe_dt(s):
    if not s: return None
    try: return dtp.parse(s, dayfirst=True)
    except Exception: return None

def parse_date_range_from_text(text: str):
    """
    Heuristics to pull a date or date-range from free text lines like:
    - '20–25 Aug 2025'
    - '20 Aug 2025 to 25 Aug 2025'
    - '20 Aug - 25 Aug 2025'
    Returns (start_dt, end_dt, has_time)
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    joined = " ".join(lines)

    # Full range with both days/months
    m = re.search(
        rf"\b(\d{{1,2}})\s*(?:–|-|to)\s*(\d{{1,2}})\s+{MONTHS}\s+(\d{{4}})\b", joined, re.I)
    if m:
        d1, d2, mon, year = m.groups()
        start = parse_maybe_dt(f"{d1} {mon} {year}")
        end = parse_maybe_dt(f"{d2} {mon} {year}")
        if start and end and end < start:
            # cross-month typo fallback — ignore
            pass
        if start and end:
            return start, end, False

    # Range where month written only once at the end, or mixed wording "to"
    m = re.search(
        rf"\b(\d{{1,2}}\s+{MONTHS}\s+\d{{4}})\s*(?:–|-|to)\s*(\d{{1,2}}\s+{MONTHS}?\s*\d{{4}}?)\b", joined, re.I)
    if m:
        s1, s2 = m.groups()[0], m.group(2)
        s = parse_maybe_dt(s1)
        e = parse_maybe_dt(s2)
        if s and e:
            return s, e, False

    # Single explicit datetime with time
    m = re.search(
        rf"\b(\d{{1,2}}\s+{MONTHS}\s+\d{{4}})\s+(\d{{1,2}}:\d{{2}})"
        rf"(?:\s*[-–]\s*(\d{{1,2}}:\d{{2}}))?", joined, re.I)
    if m:
        dpart, t1, t2 = m.group(1), m.group(2), m.group(3)
        s = parse_maybe_dt(f"{dpart} {t1}")
        e = parse_maybe_dt(f"{dpart} {t2}") if t2 else None
        return s, e, True

    # Single date only
    m = re.search(rf"\b(\d{{1,2}}\s+{MONTHS}\s+\d{{4}})\b", joined, re.I)
    if m:
        d = parse_maybe_dt(m.group(1))
        if d:
            return d, None, False

    return None, None, False

def event_uid(url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"{h[:32]}@fid-event-scrape"

async def scrape_one(page, url):
    await page.goto(url, wait_until="networkidle")

    # Strip obvious chrome (header/footer/nav) and grab a targeted main block
    full = await page.evaluate("""
        () => {
          for (const sel of ['header','footer','nav','.navbar','.site-header','.site-footer']) {
            document.querySelectorAll(sel).forEach(n=>n.remove());
          }
          const main = document.querySelector('main') || document.querySelector('#main') ||
                       document.querySelector('.container, .content, .page-content') || document.body;
          const text = (main.innerText || '').trim();
          const h1 = (document.querySelector('h1')?.innerText || '').trim();
          // Try also breadcrumbs / subheads for a specific title
          const h2 = (document.querySelector('h2')?.innerText || '').trim();
          const titleCandidates = [h1, h2, document.title].filter(Boolean);
          return { text, titleCandidates };
        }
    """)
    text = clean_text(full["text"])
    titleCandidates = full["titleCandidates"]

    # Prefer non-generic title
    title = None
    for cand in titleCandidates:
        if cand and "International Chess Federation" not in cand:
            title = cand
            break
    if not title:
        title = titleCandidates[0] if titleCandidates else "FIDE Event"

    # Try to parse a dictionary of labelled fields (Date, Venue, Location, City, Country, etc.)
    fields = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z ]{3,30})\s*:\s*(.+)$", line)
        if m:
            key = m.group(1).strip().lower()
            val = m.group(2).strip()
            fields[key] = val

    # Build location from best available cues
    location = first_nonempty(
        fields.get("venue"),
        fields.get("location"),
        fields.get("city"),
        fields.get("place"),
        fields.get("country"),
    ) or ""

    # Dates: prefer explicit fields; otherwise derive from text
    start_dt = end_dt = None
    has_time = False

    date_val = first_nonempty(fields.get("date"), fields.get("dates"))
    start_val = first_nonempty(fields.get("start"), fields.get("start date"), fields.get("from"))
    end_val = first_nonempty(fields.get("end"), fields.get("end date"), fields.get("to"))

    if date_val:
        s, e, ht = parse_date_range_from_text(date_val)
        start_dt, end_dt, has_time = s, e, ht

    if (not start_dt) and (start_val or end_val):
        s = parse_maybe_dt(start_val) if start_val else None
        e = parse_maybe_dt(end_val) if end_val else None
        # If only a start date is given and it's a range like "20–25 Aug 2025" in start_val
        if (not s) and start_val:
            s2, e2, ht2 = parse_date_range_from_text(start_val)
            s, e, has_time = s2, e2, ht2
        start_dt, end_dt = s, e

    if not start_dt:
        s, e, ht = parse_date_range_from_text(text)
        start_dt, end_dt, has_time = s, e, ht

    # Build a clean description block with just the event info (not site nav)
    ordered_keys = [
        "date", "dates", "start", "end", "venue", "location", "city", "country",
        "organizer", "chief arbiter", "time control", "format", "prizes", "contact", "website", "email", "phone"
    ]
    lines = []
    lines.append(f"Source: {url}")
    for k in ordered_keys:
        if k in fields:
            label = k.title()
            lines.append(f"{label}: {fields[k]}")
    # If we didn’t catch anything, include the first 1500 chars of the main text as a fallback
    if len(lines) <= 1:
        snippet = text[:1500] + ("…" if len(text) > 1500 else "")
        lines.append(snippet)

    description = "\n".join(lines).strip()

    return {
        "title": title,
        "location": location,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "has_time": has_time,
        "description": description,
        "url": url,
    }

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
            if r["has_time"] or (isinstance(r["start_dt"], datetime) and r["start_dt"].time() != datetime.min.time()):
                ev.begin = r["start_dt"]
                if r["end_dt"]:
                    ev.end = r["end_dt"]
            else:
                # All-day event(s)
                start_d = r["start_dt"].date() if isinstance(r["start_dt"], datetime) else r["start_dt"]
                if r["end_dt"]:
                    end_d = r["end_dt"].date() if isinstance(r["end_dt"], datetime) else r["end_dt"]
                else:
                    end_d = start_d
                # ics expects DTEND exclusive for all-day; add one day
                ev.begin = start_d
                ev.make_all_day()
                ev.end = (end_d + timedelta(days=1))
        else:
            # No dates parsed → create all-day placeholder for today
            ev.begin = date.today()
            ev.make_all_day()

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
            results.append(await scrape_one(page, url))
        await browser.close()
    write_ics(results)

if __name__ == "__main__":
    asyncio.run(main())