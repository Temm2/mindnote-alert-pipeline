"""Collects upcoming remote/virtual business & startup events from
Luma and Eventbrite, and logs them to the events_log Google Sheet tab.
Run on a schedule by GitHub Actions.
"""
import os
import datetime
import requests
from common import get_sheet, already_logged


def fetch_luma():
    key = os.environ.get("LUMA_API_KEY")
    if not key:
        return []
    resp = requests.get(
        "https://public-api.lu.ma/v1/calendar/list-events",
        headers={"x-luma-api-key": key},
        params={"category": "business,tech,startup"},
        timeout=30,
    )
    resp.raise_for_status()
    items = []
    for entry in resp.json().get("entries", []):
        ev = entry["event"]
        items.append({"name": ev["name"], "when": ev["start_at"], "link": f"https://lu.ma/{ev['url']}"})
    return items


def fetch_eventbrite():
    token = os.environ.get("EVENTBRITE_TOKEN")
    if not token:
        return []
    resp = requests.get(
        "https://www.eventbriteapi.com/v3/events/search/",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": "startup founders networking", "online_event": "true", "sort_by": "date"},
        timeout=30,
    )
    resp.raise_for_status()
    items = []
    for ev in resp.json().get("events", []):
        if not ev.get("online_event"):
            continue
        items.append({"name": ev["name"]["text"], "when": ev["start"]["utc"], "link": ev["url"]})
    return items


def main():
    events = fetch_luma() + fetch_eventbrite()
    print(f"Collected {len(events)} candidate events")

    sheet = get_sheet("events_log")
    today = datetime.date.today().isoformat()
    logged = 0

    for ev in events:
        if already_logged(sheet, ev["name"], column="event_name"):
            continue
        sheet.append_row([today, ev["name"], ev["when"], ev["link"], "no"])
        logged += 1

    print(f"Logged {logged} events")


if __name__ == "__main__":
    main()
