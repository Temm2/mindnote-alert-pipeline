"""Shared helpers used by all collector/sender scripts.
All secrets come from environment variables, set as GitHub Actions
repo secrets (see README-github-actions-setup.md).
"""
import os
import re
import json
import requests
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ["SHEET_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CLASSIFY_SYSTEM_PROMPT = """You are a strict classifier for a B2B lead-alert system. Decide whether the text contains an EXPLICIT statement that a specific, named company or founder is currently seeking a specific product, service, vendor, or partner.

Rules:
- The company/founder must be identifiable, not a vague "we".
- The ask must be specific enough to act on (e.g. "a fractional CFO", not "always looking to connect").
- General employee hiring posts do NOT qualify.
- Sarcasm, jokes, hypotheticals, or past-tense stories do NOT qualify.

Respond with ONLY valid JSON, nothing else:
{"match": true, "company": "...", "seeking": "..."} or {"match": false}
"""


def get_sheet(tab_name: str):
    """Return a gspread worksheet handle for the given tab."""
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet(tab_name)


def classify_with_claude(source: str, url: str, text: str) -> dict:
    """Ask Claude whether this text is an explicit 'X looking for Y' ask."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-5",
            "max_tokens": 300,
            "system": CLASSIFY_SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": f"Source: {source}\nURL: {url}\nText: {text}"}
            ],
        },
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json()["content"][0]["text"]
    # Claude sometimes wraps JSON in markdown code fences even when told
    # not to — strip those before parsing, and fall back to extracting
    # the first {...} block if there's any other stray text around it.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        print(f"Could not parse classification response, treating as no-match. Raw: {raw[:200]}")
        return {"match": False}


def already_logged(sheet, value: str, column: str = "company") -> bool:
    """Simple lookback dedupe: has this value already been logged in
    the given column? Checks the last 200 rows for speed."""
    records = sheet.get_all_records()
    value_lower = value.strip().lower()
    return any(r.get(column, "").strip().lower() == value_lower for r in records[-200:])


def extract_jsonld_events(html: str, base_url: str) -> list:
    """Pull schema.org Event entries out of a page's JSON-LD blocks.
    Event platforms embed this structured data for Google's search
    results, so it's a far more stable scrape target than visual
    HTML/CSS, which changes on every redesign."""
    events = []
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            # Some sites nest events inside an ItemList
            nested = item.get("itemListElement", [])
            pool = [item] + [n.get("item", n) for n in nested if isinstance(n, dict)]
            for node in pool:
                if not isinstance(node, dict):
                    continue
                if node.get("@type") in ("Event", "BusinessEvent", "SocialEvent", "EducationEvent"):
                    name = node.get("name")
                    start = node.get("startDate")
                    url = node.get("url") or base_url
                    if name and start:
                        events.append({"name": name, "when": start, "link": url})
    return events


def send_telegram(message: str):
    if not message.strip():
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
        timeout=30,
    ).raise_for_status()
