"""PRIVATE-lead collector for companies/startups that need AI notetaking
or transcription, from non-procurement sources. All free, no keys needed:
  1. Stack Exchange "Software Recommendations" (people asking for tools)
  2. Hacker News comments/asks ("recommend a transcription tool...")
  3. Remote job boards (RemoteOK, We Work Remotely, Jobicy): companies
     HIRING transcriptionists / note-takers = buyers of this automation
  4. World Bank procurement notices (global, free API)
Logs to `notetaker_leads` (date, company, seeking, link, contacted).
Every source is isolated; keyword prefilter before Claude; hard cap.
"""
import re
import html
import time
import datetime
import requests
import feedparser
from common import get_sheet, classify_with_claude

MAX_CLASSIFY = 40
DAYS = 7
HEADERS = {"User-Agent": "MindNoteLeadBot/1.0"}
NOW = int(time.time())
SINCE = NOW - DAYS * 86400

KEYWORDS = re.compile(
    r"transcri|note[- ]?tak|notetak|minute[- ]?tak|meeting (notes|summar|record|assistant|bot)|"
    r"speech[- ]to[- ]text|voice[- ]to[- ]text|dictation|captioning|otter\.ai|fireflies|"
    r"fathom|granola|read\.ai|tl;?dv|avoma|sembly|rev\.com|trint|descript|"
    r"call recording|interview (record|transcri)",
    re.I,
)

ASK_PROMPT = """You are a strict classifier for a sales-lead system for an AI meeting notetaker / transcription product.
Return a match ONLY if the text shows a person or company with a genuine, CURRENT need and intent to adopt a tool or vendor for AI notetaking, meeting summaries, transcription, or speech-to-text (asking for recommendations, comparing or leaving competitors like Otter/Fireflies/Fathom, or requesting a vendor/quote).
Do NOT match: tutorials, product launches by vendors, opinion pieces, past-tense stories, unrelated 'notes' apps, or people selling a service.
Respond with ONLY valid JSON: {"match": true, "company": "<person, handle or org>", "seeking": "<short need>"} or {"match": false}
"""

JOB_PROMPT = """You are a strict classifier for a sales-lead system for an AI meeting notetaker / transcription product.
The text is a job posting. Return a match ONLY if the role is primarily transcription, note-taking, minute-taking, captioning, or meeting-documentation work (i.e. the employer is paying humans for what our product automates).
Do NOT match general assistant/admin/ops roles where this is a minor duty, or transcription-tool engineering jobs.
Respond with ONLY valid JSON: {"match": true, "company": "<employer>", "seeking": "<role and what they need>"} or {"match": false}
"""

TENDER_PROMPT = """You are a strict classifier for a sales-lead system for an AI meeting notetaker / transcription product.
Match ONLY if this procurement notice is an OPEN or upcoming request for AI notetaking/meeting-summary software, transcription (automated or human), speech-to-text, minute-taking, or captioning services. Award notices, translation/interpreting only, and unrelated goods do NOT match.
Respond with ONLY valid JSON: {"match": true, "company": "<buyer>", "seeking": "<short need>"} or {"match": false}
"""


def clean(s, n=1500):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()[:n]


def item(source, kind, prompt, who, title, text, link):
    return {"source": source, "kind": kind, "prompt": prompt, "who": who,
            "title": title, "text": text, "link": link}


def fetch_stackexchange():
    out = []
    for q in ["transcription", "meeting notes", "speech to text", "note taker", "meeting recording summary"]:
        r = requests.get("https://api.stackexchange.com/2.3/search/advanced", params={
            "site": "softwarerecs", "q": q, "sort": "creation", "order": "desc",
            "fromdate": SINCE, "pagesize": 30, "filter": "withbody"},
            headers=HEADERS, timeout=30)
        if r.status_code >= 400:
            print("StackExchange error:", r.status_code, r.text[:150])
            continue
        for it in r.json().get("items", []):
            out.append(item("StackExchange", "Community", ASK_PROMPT,
                            (it.get("owner") or {}).get("display_name", ""),
                            clean(it.get("title"), 200), clean(it.get("body")), it.get("link", "")))
        time.sleep(1)
    return out


def fetch_hn_comments():
    out = []
    for q in ["looking for transcription tool", "recommend meeting notes tool",
              "alternative to Otter", "AI note taker recommendation", "transcription vendor"]:
        r = requests.get("https://hn.algolia.com/api/v1/search_by_date", params={
            "query": q, "tags": "comment", "numericFilters": f"created_at_i>{SINCE}",
            "hitsPerPage": 30}, headers=HEADERS, timeout=30)
        if r.status_code >= 400:
            print("HN error:", r.status_code)
            continue
        for h in r.json().get("hits", []):
            out.append(item("HN", "Community", ASK_PROMPT, h.get("author", ""),
                            clean(h.get("story_title"), 200), clean(h.get("comment_text")),
                            f"https://news.ycombinator.com/item?id={h.get('objectID')}"))
    return out


def fetch_remoteok():
    out = []
    r = requests.get("https://remoteok.com/api", headers=HEADERS, timeout=30)
    if r.status_code >= 400:
        print("RemoteOK error:", r.status_code)
        return out
    for j in r.json():
        if not isinstance(j, dict) or "position" not in j:
            continue
        out.append(item("RemoteOK", "Hiring", JOB_PROMPT, j.get("company", ""),
                        j.get("position", ""), clean(j.get("description")), j.get("url", "")))
    return out


def fetch_wwr():
    out = []
    feed = feedparser.parse("https://weworkremotely.com/remote-jobs.rss", request_headers=HEADERS)
    for e in feed.entries:
        title = e.get("title", "")
        company, _, role = title.partition(":")
        out.append(item("WeWorkRemotely", "Hiring", JOB_PROMPT, company.strip(),
                        role.strip() or title, clean(e.get("summary")), e.get("link", "")))
    return out


def fetch_jobicy():
    out = []
    for tag in ["transcription", "note taker", "captioning"]:
        r = requests.get("https://jobicy.com/api/v2/remote-jobs",
                         params={"count": 50, "tag": tag}, headers=HEADERS, timeout=30)
        if r.status_code >= 400:
            print("Jobicy error:", r.status_code)
            continue
        for j in r.json().get("jobs", []):
            out.append(item("Jobicy", "Hiring", JOB_PROMPT, j.get("companyName", ""),
                            j.get("jobTitle", ""), clean(j.get("jobDescription")), j.get("url", "")))
    return out


def fetch_worldbank():
    out = []
    for q in ["transcription", "minutes", "speech to text", "captioning"]:
        r = requests.get("https://search.worldbank.org/api/v2/procnotices", params={
            "format": "json", "qterm": q, "rows": 30, "srt": "submission_date", "order": "desc"},
            headers=HEADERS, timeout=30)
        if r.status_code >= 400:
            print("WorldBank error:", r.status_code)
            continue
        notices = r.json().get("procnotices", [])
        notices = notices.values() if isinstance(notices, dict) else notices
        for n in notices:
            nid = n.get("id", "")
            out.append(item("World Bank", "Tender", TENDER_PROMPT,
                            n.get("contact_organization") or n.get("project_name", ""),
                            clean(n.get("bid_description") or n.get("notice_text"), 200),
                            clean(n.get("notice_text") or n.get("bid_description")),
                            f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}"))
    return out


def main():
    cands = []
    for fn in (fetch_stackexchange, fetch_hn_comments, fetch_remoteok, fetch_wwr,
               fetch_jobicy, fetch_worldbank):
        try:
            got = fn()
            print(f"{fn.__name__}: {len(got)} raw")
            cands += got
        except Exception as e:
            print(f"{fn.__name__} failed: {e}")

    cands = [c for c in cands if c["link"] and KEYWORDS.search(f"{c['title']} {c['text']}")]
    print(f"{len(cands)} pass keyword prefilter")

    sheet = get_sheet("notetaker_leads")
    seen = set(v.strip() for v in sheet.col_values(4))
    today = datetime.date.today().isoformat()
    classified = logged = 0
    for c in cands:
        if c["link"] in seen:
            continue
        if classified >= MAX_CLASSIFY:
            print("Classification cap reached")
            break
        classified += 1
        res = classify_with_claude(
            c["source"], c["link"],
            f"Poster/Company: {c['who']}\nTitle: {c['title']}\n{c['text']}",
            system_prompt=c["prompt"])
        if res.get("match"):
            sheet.append_row([today, res.get("company") or c["who"],
                              f"[{c['kind']} · {c['source']}] {res.get('seeking') or c['title']}",
                              c["link"], "no"])
            seen.add(c["link"])
            logged += 1
    print(f"Classified {classified}, logged {logged}")


if __name__ == "__main__":
    main()

