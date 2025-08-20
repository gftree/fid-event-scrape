# FIDE Event Pages → ICS (one-time)

This repo builds a single ICS file from specific FIDE event pages.

## How it works
- The GitHub Action runs a headless browser (Playwright) to load each URL (which renders via JavaScript).
- It extracts title/date/location when available and always includes:
  - The **source URL**
  - A **full-text dump** of the page in the event DESCRIPTION
- Output: `docs/fide_events.ics`

## Run in GitHub (no local Python needed)
1. Go to **Settings → Pages → Build and deployment** and choose **GitHub Actions**.
2. Open **Actions → Build ICS (manual)** and **Run workflow**.
3. After it completes, your file is here:

4. https://.github.io/fid-calendar-scrape/fide_events.ics

5. ## Edit URLs
Update the `URLS` list in `scraper/scrape_fide_calendar.py`.
