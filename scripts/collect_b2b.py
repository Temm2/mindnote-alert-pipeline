"""Collects candidate 'X is looking for Y' posts from free sources,
classifies each with Claude, and logs confirmed matches to the
b2b_log Google Sheet tab. Run on a schedule by GitHub Actions.
"""
import os
import datetime
import feedparser
import requests
from common import get_sheet, classify_with_claude, already_logged

QUERY = '"looking for a" OR "anyone recommend" OR "need a"'
SUBREDDITS = "startups+Entrepreneur+SaaS+smallbusiness"


def fetch_reddit():
    resp = requests.get(
        f"https://www.reddit.com/r/{SUBREDDITS}/search.json",
        params={"q": QUERY, "sort": "new", "restrict_sr": "on", "t": "day"},
        headers={"User-Agent": "python:mindnote-alert-bot:v1.0 (by /u/mindnote_bot)"},
        timeout=30,
    )
    resp.raise_for_status()
    items = []
    for child in resp.json().get("data", {}).get("children", []):
        d = child["data"]
        items.append({
            "source": "reddit",
            "text": f"{d.get('title','')} {d.get('selftext','')}",
            "url": f"https://reddit.com{d.get('permalink','')}",
        })
    return items


def fetch_product_hunt():
    token = os.environ.get("PRODUCTHUNT_TOKEN")
    if not token:
        return []
    yesterday = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat()
    query = """
    { posts(order: NEWEST, postedAfter: "%s") {
        edges { node { name tagline url comments(first: 5) { edges { node { body } } } } }
    } }""" % yesterday
    resp = requests.post(
        "https://api.producthunt.com/v2/api/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
        timeout=30,
    )
    resp.raise_for_status()
    items = []
    for edge in resp.json().get("data", {}).get("posts", {}).get("edges", []):
        n = edge["node"]
        comments = " | ".join(c["node"]["body"] for c in n.get("comments", {}).get("edges", []))
        items.append({
            "source": "producthunt",
            "text": f"{n['name']} - {n['tagline']} {comments}",
            "url": n["url"],
        })
    return items


def fetch_indie_hackers():
    feed = feedparser.parse("https://www.indiehackers.com/starting-up/looking-for.rss")
    return [
        {"source": "indiehackers", "text": f"{e.title} {e.get('summary','')}", "url": e.link}
        for e in feed.entries
    ]


def main():
    candidates = []
    for fetch_fn in (fetch_reddit, fetch_product_hunt, fetch_indie_hackers):
        try:
            candidates += fetch_fn()
        except requests.RequestException as e:
            print(f"{fetch_fn.__name__} failed, skipping this source: {e}")
    print(f"Collected {len(candidates)} raw candidates")

    sheet = get_sheet("b2b_log")
    today = datetime.date.today().isoformat()
    logged = 0

    for item in candidates:
        result = classify_with_claude(item["source"], item["url"], item["text"])
        if not result.get("match"):
            continue
        company = result.get("company", "").strip()
        if not company or already_logged(sheet, company):
            continue
        sheet.append_row([today, company, result.get("seeking", ""), item["url"], "no"])
        logged += 1

    print(f"Logged {logged} new matches")


if __name__ == "__main__":
    main()
