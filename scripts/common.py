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

CLASSIFY_SYSTEM_PROMPT = """You are a strict classifier for a B2B lead-alert system aimed at a startup/tech/professional-services community. Decide whether the text contains an EXPLICIT statement that a company, startup, scaleup, unicorn, procurement team, consultancy, or agency is currently seeking a PAID, professional vendor, service, or agency engagement from another business (another startup, scaleup, unicorn, vendor, agency, or freelance professional).

Rules:
- The poster must be speaking for an identifiable business entity — their own startup, project, or company — not an individual seeking a personal favor. A formal registered company name is NOT required: if no company name is given, use their username or project/product name as "company" instead of rejecting the post for that reason alone.
- Reject asks for personal, non-business services — e.g. home renovation, personal errands, or anything a private individual would hire a contractor for on their own property or personal life, even if phrased professionally.
- Assume the engagement is PAID at standard market rates by default — the poster does NOT need to state a dollar amount for this to qualify. Only reject if the post explicitly signals the work is unpaid, volunteer, for exposure only, or equity/profit-share/revenue-share-only.
- Equity, profit-share, or revenue-share-only asks do NOT qualify, even if phrased as a percentage or stake rather than the word "equity" (e.g. "join for a cut of profits" does NOT qualify) — this classifier is strictly for cash-paid engagements, not team-building or co-founder matching.
- Reject requests recruiting many individual participants, testers, panelists, or gig workers for paid micro-tasks (e.g. "we need 50 people to test our app for $5 each"). This classifier is for hiring ONE vendor, agency, or professional for a defined engagement, not crowdsourcing a workforce.
- The ask should be for a kind of service a startup, tech, or professional-services community could plausibly help with or connect the poster to — e.g. marketing, software/SaaS, development, design, consulting, recruiting, legal, PR, media/content, finance. Reject asks that are purely industrial, physical-manufacturing, or trades-based with no digital/professional-services angle (e.g. bathroom fixture manufacturing, construction contracting), even if the underlying ask is genuinely paid and legitimate.
- Reject informal, low-effort, or crowd-favor requests that don't read like a genuine business engagement (e.g. a casual "does anyone know a good X?" with no business framing, or a request explicitly framed as a favor).
- Reject vague, crowd-level statements not tied to one specific business (e.g. "we as an industry need better tools" does NOT qualify; "I'm building X and need a payments partner" DOES qualify even with no formal company name).
- The ask must be specific enough to act on (e.g. "a fractional CFO" or "an SEO agency", not "always looking to connect" or "open to opportunities").
- General employee hiring posts do NOT qualify, but paid freelancer, contractor, agency, consultancy, and vendor asks DO qualify.
- Sarcasm, jokes, hypotheticals, or past-tense stories do NOT qualify.
- A single post can list several distinct asks (e.g. office leasing, a recruiter, a visa consultant, an SEO agency, all in one post). Extract each as its own match rather than only the first one, and set "seeking" to a comma-separated summary if there are multiple.

Example of a strong match: a startup founder states their funding/valuation context for credibility (no formal company name needed), then lists several concrete paid-engagement needs like "SF office leasing," "a B2B marketing-focused recruiter," "US visa services," "an SEO/SEM agency" — this qualifies even though no company name or dollar amount is given, because the asks are specific, clearly business-to-business, implicitly paid, and relevant to a professional-services audience.

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


def classify_with_claude(source: str, url: str, text: str, system_prompt: str = None) -> dict:
    """Ask Claude whether this text matches the given system prompt's
    criteria. Defaults to the B2B CLASSIFY_SYSTEM_PROMPT above, but
    other scripts (e.g. the leads bot) can pass their own prompt."""
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
            "system": system_prompt or CLASSIFY_SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": f"Source: {source}\nURL: {url}\nText: {text}"}
            ],
        },
        timeout=30,
    )
    if not resp.ok:
        raise requests.HTTPError(
            f"{resp.status_code} error from Anthropic API: {resp.text[:500]}")
    response_json = resp.json()
    content_blocks = response_json.get("content", [])
    raw = ""
    for block in content_blocks:
        if block.get("type") == "text" and "text" in block:
            raw = block["text"]
            break
    if not raw:
        print(f"Unexpected Claude response shape, treating as no-match: {response_json}")
        return {"match": False}
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


def send_telegram(message: str, chat_id: str = None, bot_token: str = None):
    if not message.strip():
        return
    target = chat_id or TELEGRAM_CHAT_ID
    token = bot_token or TELEGRAM_BOT_TOKEN
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": target, "text": message},
        timeout=30,
    ).raise_for_status()
