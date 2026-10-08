"""Collects PUBLIC-SECTOR tenders for AI notetaking / transcription /
minute-taking / captioning vendors and logs them to the PRIVATE
`notetaker_leads` tab (date, company, seeking, link, contacted).
Sources: TED (EU), SAM.gov (US), UK Find a Tender, UK Contracts Finder,
CanadaBuys. Each source is isolated: one failing never stops the others.
Keyword prefilter runs first so Claude only sees plausible tenders.
"""
import os
import re
import io
import csv
import time
import datetime
import requests
from common import get_sheet, classify_with_claude

LOOKBACK_DAYS = 3
MAX_CLASSIFY = 40  # hard cap per run = hard cap on Claude cost

KEYWORDS = re.compile(
    r"transcri|minute[- ]?tak|minutes of|note[- ]?tak|notetak|speech[- ]to[- ]text|"
    r"speech recognition|voice recognition|dictation|captioning|closed caption|"
    r"hansard|court report|stenograph|meeting summar|meeting assistant|"
    r"verbatim report|audio to text|clerking|secretariat services",
    re.I,
)

TENDER_PROMPT = """You are a strict classifier for a sales-lead system for an AI meeting notetaker / transcription product.
Decide whether this PUBLIC TENDER notice is a buyer seeking a vendor for: AI notetaking or meeting-summary software, automated or human transcription, speech-to-text, minute-taking or meeting-minutes services, captioning, hansard or court-reporting style services.
Rules:
- Translation or interpreting only does NOT qualify.
- Award or contract-result notices do NOT qualify (we want open or upcoming opportunities).
- Unrelated goods or services that merely mention the words do NOT qualify.
Respond with ONLY valid JSON:
{"match": true, "company": "<buyer organisation>", "seeking": "<short description of what they want>"} or {"match": false}
"""

HEADERS = {"User-Agent": "MindNoteLeadBot/1.0"}
since = datetime.date.today() - datetime.timedelta(days=LOOKBACK_DAYS)


def first_text(v):
    """TED returns multilingual dicts like {'eng': '...'} or lists."""
    if isinstance(v, dict):
        v = v.get("eng") or next(iter(v.values()), "")
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = first_text(v)
    return str(v or "")


def fetch_ted():
    out = []
    query = ("(classification-cpv IN (79521000 79530000 79540000 79520000 48000000 72000000) "
             f"OR FT~(transcription OR minute-taking OR notetaking OR \"speech-to-text\")) "
             f"AND publication-date>={since.strftime('%Y%m%d')}")
    r = requests.post(
        "https://api.ted.europa.eu/v3/notices/search",
        json={"query": query,
              "fields": ["publication-number", "notice-title", "buyer-name", "description-lot"],
              "page": 1, "limit": 100, "scope": "ALL"},
        headers=HEADERS, timeout=60,
    )
    if r.status_code >= 400:
        print("TED error:", r.status_code, r.text[:300])
        return out
    for n in r.json().get("notices", []):
        pub = n.get("publication-number")
        out.append({
            "source": "TED",
            "buyer": first_text(n.get("buyer-name")),
            "title": first_text(n.get("notice-title")),
            "text": first_text(n.get("description-lot"))[:1500],
            "link": f"https://ted.europa.eu/en/notice/-/detail/{pub}",
        })
    return out


def fetch_sam():
    key = os.environ.get("SAM_GOV_API_KEY")
    if not key:
        print("SAM.gov skipped: no SAM_GOV_API_KEY")
        return []
    out = []
    # public keys are limited (~10 calls/day) -> only 5 title queries
    for term in ["transcription", "court reporting", "minutes", "speech to text", "captioning"]:
        r = requests.get(
            "https://api.sam.gov/opportunities/v2/search",
            params={"api_key": key, "limit": 100, "title": term,
                    "postedFrom": since.strftime("%m/%d/%Y"),
                    "postedTo": datetime.date.today().strftime("%m/%d/%Y")},
            headers=HEADERS, timeout=60,
        )
        if r.status_code >= 400:
            print("SAM error:", r.status_code, r.text[:200])
            continue
        for o in r.json().get("opportunitiesData", []) or []:
            out.append({
                "source": "SAM.gov",
                "buyer": (o.get("fullParentPathName") or "").replace(".", " / "),
                "title": o.get("title", ""),
                "text": "",
                "link": o.get("uiLink", ""),
            })
        time.sleep(1)
    return out


def _ocds(url, params, link_base, source):
    out = []
    r = requests.get(url, params=params, headers=HEADERS, timeout=60)
    if r.status_code >= 400:
        print(f"{source} error:", r.status_code, r.text[:200])
        return out
    for rel in r.json().get("releases", []):
        t = rel.get("tender", {}) or {}
        rid = rel.get("id") or rel.get("ocid", "")
        notice_id = str(rid).split("-")[0] if source == "Contracts Finder" else rel.get("ocid", "")
        out.append({
            "source": source,
            "buyer": (rel.get("buyer") or {}).get("name", ""),
            "title": t.get("title", ""),
            "text": (t.get("description") or "")[:1500],
            "link": f"{link_base}{notice_id}",
        })
    return out


def fetch_find_a_tender():
    return _ocds(
        "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages",
        {"updatedFrom": f"{since.isoformat()}T00:00:00", "limit": 100, "stages": "tender"},
        "https://www.find-tender.service.gov.uk/Notice/", "Find a Tender")


def fetch_contracts_finder():
    return _ocds(
        "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search",
        {"publishedFrom": since.isoformat(), "publishedTo": datetime.date.today().isoformat(),
         "stages": "tender", "size": 100},
        "https://www.contractsfinder.service.gov.uk/Notice/", "Contracts Finder")


def fetch_canadabuys():
    r = requests.get(
        "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv",
        headers=HEADERS, timeout=120)
    if r.status_code >= 400:
        print("CanadaBuys error:", r.status_code)
        return []
    reader = csv.DictReader(io.StringIO(r.content.decode("utf-8-sig", errors="replace")))

    def col(row, *parts):
        for k in row:
            kl = k.lower()
            if all(p in kl for p in parts):
                return row[k] or ""
        return ""

    out = []
    for row in reader:
        pub = col(row, "publicationdate")[:10]
        if pub and pub < since.isoformat():
            continue
        out.append({
            "source": "CanadaBuys",
            "buyer": col(row, "contractingentity", "eng") or col(row, "organization", "eng") or col(row, "department", "eng"),
            "title": col(row, "title", "eng"),
            "text": col(row, "description", "eng")[:1500],
            "link": col(row, "noticeurl", "eng"),
        })
    return out


def main():
    candidates = []
    for fn in (fetch_ted, fetch_sam, fetch_find_a_tender, fetch_contracts_finder, fetch_canadabuys):
        try:
            got = fn()
            print(f"{fn.__name__}: {len(got)} raw")
            candidates += got
        except Exception as e:
            print(f"{fn.__name__} failed: {e}")

    # keyword prefilter (free) before any Claude call
    cands = [c for c in candidates if c["link"] and KEYWORDS.search(f"{c['title']} {c['text']}")]
    print(f"{len(cands)} pass keyword prefilter")

    sheet = get_sheet("notetaker_leads")
    seen = set(v.strip() for v in sheet.col_values(4))
    today = datetime.date.today().isoformat()
    logged = classified = 0
    for c in cands:
        if c["link"] in seen:
            continue
        if classified >= MAX_CLASSIFY:
            print("Classification cap reached")
            break
        classified += 1
        res = classify_with_claude(
            c["source"], c["link"],
            f"Buyer: {c['buyer']}\nTitle: {c['title']}\n{c['text']}",
            system_prompt=TENDER_PROMPT)
        if res.get("match"):
            sheet.append_row([today, res.get("company") or c["buyer"],
                              f"[Tender · {c['source']}] {res.get('seeking') or c['title']}",
                              c["link"], "no"])
            seen.add(c["link"])
            logged += 1
    print(f"Classified {classified}, logged {logged} tender leads")


if __name__ == "__main__":
    main()
