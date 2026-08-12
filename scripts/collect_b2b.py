"""Collects candidate 'X is looking for Y' posts from free sources,
classifies each with Claude, and logs confirmed matches to the
b2b_log Google Sheet tab. Run on a schedule by GitHub Actions.

Sources: Reddit, Product Hunt, Indie Hackers, Hacker News, Bluesky,
Mastodon, StackShare, BetaList. Each fetch_* function is isolated —
if one source fails or a site changes its layout, the rest still run.
"""
import os
import re
import datetime
import feedparser
import requests
from common import get_sheet, classify_with_claude, already_logged

QUERY = '"looking for a" OR "anyone recommend" OR "need a"'
SUBREDDITS = "startups+Entrepreneur+SaaS+smallbusiness+msp+sysadmin+forhire"
REDDIT_UA = "python:mindnote-alert-bot:v1.0 (by /u/mindnote_bot)"

MASTODON_TARGETS = [
    ("mastodon.social", "startup"),
    ("mastodon.social", "SaaS"),
    ("indieweb.social", "buildinpublic"),
]
BETALIST_PAGES = [
    "https://betalist.com/browse/other/startups",
    "https://betalist.com/",
]


def fetch_reddit():
    resp = requests.get(
        f"https://www.reddit.com/r/{SUBREDDITS}/search.json",
        params={"q": QUERY, "sort": "new", "restrict_sr": "on", "t": "day"},
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


def fetch_product_hunt():
    token = os.environ.get("PRODUCTHUNT_TOKEN")
    if not token:
        return []
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=4)).isoformat()
    query = """
    { posts(order: NEWEST, postedAfter: "%s") {
        edges { node { name tagline url comments(first: 5) { edges { node { body } } } } }
    } }""" % since
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
    """The old RSS URL this used to hit doesn't actually exist — it
    silently redirected to the homepage and returned 0 items every
    run. The real, live source is Indie Hackers' public 'Partner Up'
    group page, which is full of genuine 'looking for X' posts and
    needs no login."""
    resp = requests.get(
        "https://www.indiehackers.com/group/looking-to-partner-up",
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MindNoteAlertBot/1.0)"},
    )
    resp.raise_for_status()
    html = resp.text
    items = []
    seen_urls = set()
    for m in re.finditer(r'href="(/post/[a-f0-9]+)"[^>]*>([^<]{5,150})<', html):
        path, title = m.groups()
        # Strip trailing relative-age markers like "2d" / "3m" that
        # sit right after the title text in the link.
        title = re.sub(r"\s*\d+[dhwm]$", "", title).strip()
        url = f"https://www.indiehackers.com{path}"
        if url in seen_urls or not title:
            continue
        seen_urls.add(url)
        items.append({"source": "indiehackers", "text": title, "url": url})
    return items


def fetch_hacker_news():
    """Uses Algolia's official free HN Search API, no key required."""
    items = []
    cutoff = int(datetime.datetime.utcnow().timestamp()) - 86400 * 2
    for query in ("looking for a", "seeking freelancer"):
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"query": query, "tags": "story", "numericFilters": f"created_at_i>{cutoff}"},
            timeout=30,
        )
        resp.raise_for_status()
        for hit in resp.json().get("hits", []):
            title = hit.get("title") or ""
            items.append({
                "source": "hackernews",
                "text": f"{title} {hit.get('story_text') or ''}",
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            })
    return items


def fetch_bluesky():
    """Needs a free Bluesky app password (not a paid key)."""
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
    for query in ('"looking for a"', '"need a" vendor OR partner OR tool'):
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
    """Uses TwitterAPI.io (a paid, low-cost third-party service — see
    README) to search X/Twitter for explicit asks. Not free like the
    other sources, but cheap: ~$0.15 per 1,000 tweets read."""
    api_key = os.environ.get("TWITTERAPI_IO_KEY")
    if not api_key:
        return []

    items = []
    queries = [
        '"looking for a" (agency OR vendor OR partner OR consultant) -filter:retweets',
        '"currently looking for" -filter:retweets',
    ]
    for query in queries:
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


def fetch_stackshare():
    """Best-effort scrape of StackShare's public feed. StackShare is a
    small/declining platform so expect low volume; this is a long-tail
    source, not a primary one."""
    resp = requests.get("https://stackshare.io/feed", timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; MindNoteAlertBot/1.0)"})
    resp.raise_for_status()
    html = resp.text
    items = []
    for m in re.finditer(r'href="(/companies/[^"]+)"[^>]*>([^<]{5,120})<', html):
        path, text = m.groups()
        items.append({
            "source": "stackshare",
            "text": text.strip(),
            "url": f"https://stackshare.io{path}",
        })
    return items[:30]


def fetch_betalist():
    items = []
    for url in BETALIST_PAGES:
        resp = requests.get(url, timeout=30,
                             headers={"User-Agent": "Mozilla/5.0 (compatible; MindNoteAlertBot/1.0)"})
        resp.raise_for_status()
        html = resp.text
        for m in re.finditer(r'/startups/([a-z0-9\-]+)"[^>]*>\s*([^<]{2,60})<.*?<p[^>]*>([^<]{5,160})<',
                              html, re.DOTALL):
            slug, name, tagline = m.groups()
            items.append({
                "source": "betalist",
                "text": f"{name.strip()} - {tagline.strip()}",
                "url": f"https://betalist.com/startups/{slug}",
            })
    return items[:30]


def main():
    sources = (
        fetch_reddit, fetch_product_hunt, fetch_indie_hackers,
        fetch_hacker_news, fetch_bluesky, fetch_mastodon,
        fetch_stackshare, fetch_betalist, fetch_x_twitter,
    )
    candidates = []
    for fetch_fn in sources:
        try:
            found = fetch_fn()
            print(f"{fetch_fn.__name__}: {len(found)} candidates")
            candidates += found
        except Exception as e:
            print(f"{fetch_fn.__name__} failed, skipping this source: {e}")

    print(f"Collected {len(candidates)} raw candidates total")

    sheet = get_sheet("b2b_log")
    today = datetime.date.today().isoformat()
    logged = 0
    debug_shown = 0

    for item in candidates:
        try:
            result = classify_with_claude(
                item.get("source", "unknown"), item.get("url", ""), item.get("text", ""))
        except Exception as e:
            print(f"Classification failed for one item, skipping: {e}")
            continue
        if debug_shown < 5:
            print(f"DEBUG sample [{item['source']}]: text={item['text'][:100]!r} -> result={result}")
            debug_shown += 1
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
