# MindNote Alert Pipeline — GitHub Actions Edition (100% free)

No server, no n8n subscription. GitHub runs the schedule for you,
free, forever (2,000 free minutes/month — this uses a few minutes/day).

---

## 1. Create a GitHub repo

- New repo (private is fine) → upload this whole folder, keeping the
  `.github/workflows/` folder structure intact — GitHub finds workflows
  by that exact path.

## 2. Create your free accounts / API keys

| Service | Where | What you get |
|---|---|---|
| Anthropic (Claude) | console.anthropic.com → API Keys | `ANTHROPIC_API_KEY` |
| Telegram Bot | message **@BotFather** on Telegram → `/newbot` | `TELEGRAM_BOT_TOKEN` |
| Telegram Chat ID | message your new bot once, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser | `TELEGRAM_CHAT_ID` (the number in the response) |
| Product Hunt | api.producthunt.com/v2/docs → create token | `PRODUCTHUNT_TOKEN` |
| Luma | lu.ma → settings → API | `LUMA_API_KEY` |
| Eventbrite | eventbrite.com/platform/api → create app | `EVENTBRITE_TOKEN` |
| Google Cloud service account | see step 3 below | `GOOGLE_SERVICE_ACCOUNT_JSON` |

Reddit needs no key — the script uses Reddit's public read-only JSON
endpoint.

## 3. Set up the Google Sheet (free dedupe log)

1. Create a Google Sheet called `MindNote Alerts` with two tabs:
   - `b2b_log` — headers in row 1: `date | company | seeking | source_url | posted`
   - `events_log` — headers in row 1: `date | event_name | when | link | posted`
2. Go to **console.cloud.google.com** → create a project (free) →
   enable the **Google Sheets API** → **Credentials → Create
   credentials → Service account** → create a JSON key and download it.
3. Open the downloaded JSON, copy the `client_email` value, and
   **share your Google Sheet with that email** (Editor access) — this
   is how the script is allowed to read/write it without you logging in.
4. Copy the Sheet's ID from its URL (the long string between `/d/`
   and `/edit`) — that's your `SHEET_ID`.

## 4. Add everything as GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add each of these one at a time:

- `SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON` — paste the *entire contents* of the JSON key file
- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `PRODUCTHUNT_TOKEN`
- `LUMA_API_KEY`
- `EVENTBRITE_TOKEN`

## 5. Turn it on

Nothing else to run — the four workflows in `.github/workflows/`
start firing on their schedules the moment they're on GitHub's default
branch. To test one immediately instead of waiting for the schedule:
**Actions tab → pick a workflow → Run workflow** (the
`workflow_dispatch` trigger in each file enables this button).

## 6. Daily habit

Check Telegram. Copy-paste the batch into the right WhatsApp group.
That's the only manual step, and it's the same one flagged earlier —
kept manual specifically to stay WhatsApp-compliant.

---

### What runs when (all times UTC — edit the cron lines in each
`.yml` file if you want different local times)

| Workflow | Schedule |
|---|---|
| Collect B2B Opportunities | every 4 hours |
| Collect Virtual Events | 6am & 6pm |
| Send B2B Batch | 1pm, 5pm, 10pm (3x/day) |
| Send Events Batch | 12pm (1x/day, 5 events) |

### Cost reality check
- GitHub Actions: free at this volume.
- Claude API: the only real cost, and it's small — classifying a
  few hundred short posts a day runs in the range of cents/day, not
  dollars.
- Everything else (Reddit, Telegram, Sheets, Luma, Eventbrite,
  Product Hunt): free tiers, no card needed for most.
