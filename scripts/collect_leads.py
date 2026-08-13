"""Collects candidates who might need an AI notetaker/meeting-transcription
tool, classifies each with Claude using a narrow, product-specific
prompt, and logs confirmed leads to the notetaker_leads Google Sheet
tab. This is a PRIVATE sales-lead pipeline, separate from the public
B2B community bot — different bot token, different chat, different
classifier. Run on a schedule by GitHub Actions.
"""
import os
import re
import time
import datetime
import requests
from common import get_sheet, classify_with_claude, already_logged

LEADS_CLASSIFY_SYSTEM_PROMPT = """You are a strict classifier for a sales-lead system for an AI meeting notetaker product (records/transcribes meetings, generates summaries and action items). Decide whether the text contains an EXPLICIT statement that a person, team, or company is currently looking for a tool or solution in this category — meeting notes, call transcription, meeting summaries, or similar meeting-productivity software.

Rules:
- The poster must be expressing a genuine, current need or actively asking for recommendations — not writing about the category abstractly, reviewing a specific existing tool, or discussing it academically.
- Qualifying needs include: wanting to automatically record/transcribe meetings, wanting AI-generated meeting summaries or action items, asking for recommendations for a "notetaker" or "meeting assistant" tool, complaining about manual note-taking and wanting an automated alternative, or comparing/asking about specific competitor tools (e.g. Otter.ai, Fireflies, Fathom, Grain, tl;dv) in a way that signals they're shopping for one.
- Do NOT match: general discussion of AI/productivity tools unrelated to meetings, posts that already use and are happy with a specific tool with no signal they're open to switching, or posts using "notes" in an unrelated sense (e.g. sticky notes, blog notes, code comments).
- The poster does not need to name a company — capture their username/handle as "company" if no business name is given.
- Sarcasm, jokes, hypotheticals, or past-tense stories do NOT qualify.

Respond with ONLY valid JSON, nothing else:
{"match": true, "company": "...", "seeking": "..."} or {"match": false}
"""

REDDIT_QUERY = '"meeting notes" OR "meeting transcription" OR "meeting summary" OR notetaker OR "note taking tool" OR "record meetings"'
REDDIT_SUBREDDITS = "startups+Entrepreneur+SaaS+smallbusiness+productivity+artificial+sales"
REDDIT_UA = "python:mindnote-leads-bot:v1.0 (by /u/mindnote_bot)"

MASTODON_TARGETS = [
    ("mastodon.social", "productivity"),
    ("mastodon.social", "SaaS"),
]

HN_QUERIES = ("meeting notes tool", "AI notetaker", "meeting transcription recommend")
X_QUERIES = (
    '"anyone recommend" (notetaker OR "meeting notes" OR "meeting transcription") -filter:retweets',
    '"looking for a" (notetaker OR "meeting assistant" OR "meeting transcription") -filter:retweets',
)


def fetch_reddit():
    resp = requests.get(
        f"https://www.reddit.com/r/{REDDIT_SUBREDDITS}/search.json",
        params={"q": REDDIT_QUERY, "sort": "new", "restrict_sr": "on", "t": "week"},
        headers={"User-Agent": REDDIT_UA},
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


def fetch_hacker_news():
    items = []
    cutoff = int(datetime.datetime.utcnow().timestamp()) - 86400 * 7
    for query in HN_QUERIES:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"query": query, "numericFilters": f"created_at_i>{cutoff}"},
            timeout=30,
        )
        resp.raise_for_status()
        for hit in resp.json().get("hits", []):
            title = hit.get("title") or hit.get("comment_text") or ""
            items.append({
                "source": "hackernews",
                "text": f"{title} {hit.get('story_text') or ''}",
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            })
    return items


def fetch_bluesky():
    handle = os.environ.get("BLUESKY_HANDLE")
    app_password = os.environ.get("BLUESKY_APP_PASSWORD")
    if not handle or not app_password:
        return []
    auth = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": app_password},
        timeout=30,
    )
    auth.raise_for_status()
    token = auth.json()["accessJwt"]

    items = []
    for query in ('"anyone recommend" meeting notes', '"looking for a" notetaker'):
        resp = requests.get(
            "https://bsky.social/xrpc/app.bsky.feed.searchPosts",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": query, "limit": 25, "sort": "latest"},
            timeout=30,
        )
        resp.raise_for_status()
        for post in resp.json().get("posts", []):
            text = post.get("record", {}).get("text", "")
            uri = post.get("uri", "")
            handle_part = post.get("author", {}).get("handle", "")
            post_id = uri.split("/")[-1] if uri else ""
            items.append({
                "source": "bluesky",
                "text": text,
                "url": f"https://bsky.app/profile/{handle_part}/post/{post_id}" if post_id else "https://bsky.app",
            })
    return items


def fetch_mastodon():
    items = []
    for instance, hashtag in MASTODON_TARGETS:
        resp = requests.get(
            f"https://{instance}/api/v1/timelines/tag/{hashtag}",
            params={"limit": 20},
            timeout=30,
        )
        resp.raise_for_status()
        for status in resp.json():
            text = re.sub(r"<[^>]+>", " ", status.get("content", ""))
            items.append({
                "source": "mastodon",
                "text": text,
                "url": status.get("url", f"https://{instance}"),
            })
    return items


def fetch_x_twitter():
    api_key = os.environ.get("TWITTERAPI_IO_KEY")
    if not api_key:
        print("TWITTERAPI_IO_KEY not set, skipping X/Twitter source.")
        return []
    items = []
    for i, query in enumerate(X_QUERIES):
        if i > 0:
            time.sleep(6)  # stay under the ~0.2 QPS free/bonus tier limit
        resp = requests.get(
            "https://api.twitterapi.io/twitter/tweet/advanced_search",
            params={"query": query, "queryType": "Latest"},
            headers={"X-API-Key": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        tweets = data.get("tweets", data.get("data", []))
        for t in tweets:
            text = t.get("text", "")
            tweet_id = t.get("id") or t.get("id_str", "")
            author = t.get("author", {}) or {}
            username = author.get("userName") or author.get("username", "")
            url = t.get("url") or (
                f"https://x.com/{username}/status/{tweet_id}" if username and tweet_id else ""
            )
            if text and url:
                items.append({"source": "x_twitter", "text": text, "url": url})
    return items


def main():
    sources = (fetch_reddit, fetch_hacker_news, fetch_bluesky, fetch_mastodon, fetch_x_twitter)
    candidates = []
    for fetch_fn in sources:
        try:
            found = fetch_fn()
            print(f"{fetch_fn.__name__}: {len(found)} candidates")
            candidates += found
        except Exception as e:
            print(f"{fetch_fn.__name__} failed, skipping this source: {e}")

    print(f"Collected {len(candidates)} raw candidates total")

    sheet = get_sheet("notetaker_leads")
    today = datetime.date.today().isoformat()
    logged = 0

    for item in candidates:
        try:
            result = classify_with_claude(
                item.get("source", "unknown"), item.get("url", ""), item.get("text", ""),
                system_prompt=LEADS_CLASSIFY_SYSTEM_PROMPT,
            )
        except Exception as e:
            print(f"Classification failed for one item, skipping: {e}")
            continue
        if not result.get("match"):
            continue
        company = result.get("company", "").strip()
        if not company or already_logged(sheet, company):
            continue
        sheet.append_row([today, company, result.get("seeking", ""), item.get("url", ""), "no"])
        logged += 1

    print(f"Logged {logged} new leads")


if __name__ == "__main__":
    main()
