"""Collects upcoming remote/virtual startup & business events by
scraping public pages on Luma, Eventbrite, and Meetup — none of these
require a paid API for this. Uses each page's schema.org JSON-LD
event data (the same structured data these sites feed to Google),
which is far more stable to parse than raw page layout.
Run on a schedule by GitHub Actions.
"""
import datetime
import requests
from common import get_sheet, already_logged, extract_jsonld_events

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MindNoteAlertBot/1.0; "
                  "+https://github.com/Temm2/mindnote-alert-pipeline)"
}

# Curated public calendars/pages known to carry startup & remote-friendly events.
# Add or swap URLs here any time without touching the parsing logic.
LUMA_CALENDARS = [
    "https://luma.com/buildercommunityanz",
    "https://luma.com/cursorcommunity",
    "https://luma.com/deepmind",
]
EVENTBRITE_PAGES = [
    "https://www.eventbrite.com/d/online/startup-meetup/",
    "https://www.eventbrite.com/d/online/business-networking/",
]
MEETUP_PAGES = [
    "https://www.meetup.com/topics/business-startup/us/",
    "https://www.meetup.com/topics/founders/us/",
]


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def fetch_luma():
    events = []
    for url in LUMA_CALENDARS:
        try:
            html = fetch_page(url)
            events += extract_jsonld_events(html, url)
        except requests.RequestException as e:
            print(f"Luma fetch failed for {url}: {e}")
    return events


def fetch_eventbrite():
    events = []
    for url in EVENTBRITE_PAGES:
        try:
            html = fetch_page(url)
            events += extract_jsonld_events(html, url)
        except requests.RequestException as e:
            print(f"Eventbrite fetch failed for {url}: {e}")
    return events


def fetch_meetup():
    events = []
    for url in MEETUP_PAGES:
        try:
            html = fetch_page(url)
            events += extract_jsonld_events(html, url)
        except requests.RequestException as e:
            print(f"Meetup fetch failed for {url}: {e}")
    return events


def main():
    events = fetch_luma() + fetch_eventbrite() + fetch_meetup()
    print(f"Collected {len(events)} candidate events")

    sheet = get_sheet("events_log")
    today = datetime.date.today().isoformat()
    logged = 0

    for ev in events:
        if already_logged(sheet, ev["name"], column="event_name"):
            continue
        sheet.append_row([today, ev["name"], ev["when"], ev["link"], "no"])
        logged += 1

    print(f"Logged {logged} new events")


if __name__ == "__main__":
    main()
