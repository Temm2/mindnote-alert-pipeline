"""Reads unposted rows from a sheet tab, sends one formatted batch to
Telegram, and marks those rows as posted. Called with an argument:
    python send_batch.py b2b
    python send_batch.py events

For "b2b", this also pulls in any community-submitted rows from the
b2b_submissions tab that have been manually marked approved="yes" in
the sheet, and sends them alongside the automatically-classified ones.
"""
import os
import sys
from datetime import datetime
from common import get_sheet, send_telegram

MAX_PER_BATCH = {"b2b": 15, "events": 5}
TAB_NAME = {"b2b": "b2b_log", "events": "events_log"}
SUBMISSIONS_TAB = "b2b_submissions"


def format_b2b_row(row: dict) -> str:
    return f"🔎 {row['company']} is looking for {row['seeking']} — {row['source_url']}"


def format_submission_row(row: dict) -> str:
    line = f"🤝 {row['company']} is looking for {row['seeking']}"
    if row.get("offering"):
        line += f", offering {row['offering']}"
    if row.get("link"):
        line += f" — {row['link']}"
    return line


def format_event_row(row: dict) -> str:
    when = row.get("when", "")
    try:
        when = datetime.fromisoformat(when.replace("Z", "+00:00")).strftime("%a %b %d, %H:%M UTC")
    except ValueError:
        pass
    return f"📅 {row['event_name']}\n{when}\n{row['link']}"


def get_approved_submissions():
    """Rows from b2b_submissions where a human has set approved=yes.
    Returns (row_number, row_dict) pairs, same shape as the other tabs."""
    sheet = get_sheet(SUBMISSIONS_TAB)
    records = sheet.get_all_records()
    approved = [
        (i + 2, r) for i, r in enumerate(records)
        if str(r.get("approved", "")).strip().lower() == "yes"
    ]
    return sheet, approved


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("b2b", "events"):
        print("Usage: python send_batch.py [b2b|events]")
        sys.exit(1)

    kind = sys.argv[1]
    sheet = get_sheet(TAB_NAME[kind])
    records = sheet.get_all_records()
    unposted_rows = [(i + 2, r) for i, r in enumerate(records) if r.get("posted", "no") == "no"]

    # Each queued item remembers which sheet/row it came from, so after
    # capping the combined batch we mark only the rows actually sent.
    queue = [("log", rn, r) for rn, r in unposted_rows]

    submissions_sheet = None
    if kind == "b2b":
        submissions_sheet, approved_submissions = get_approved_submissions()
        queue += [("submission", rn, r) for rn, r in approved_submissions]

    cap = MAX_PER_BATCH[kind]
    queue = queue[:cap]

    if not queue:
        print("Nothing to send")
        return

    lines = []
    for source, _, row in queue:
        if kind == "events":
            lines.append(format_event_row(row))
        elif source == "log":
            lines.append(format_b2b_row(row))
        else:
            lines.append(format_submission_row(row))
    message = "\n\n".join(lines)

    # B2B and Events can post to different Telegram destinations.
    # Falls back to the shared TELEGRAM_CHAT_ID if a kind-specific one isn't set.
    chat_id = os.environ.get(f"TELEGRAM_{kind.upper()}_CHAT_ID")
    send_telegram(message, chat_id=chat_id)

    for source, row_number, _ in queue:
        if source == "log":
            sheet.update_cell(row_number, 5, "yes")  # 'posted' is column E
        else:
            submissions_sheet.update_cell(row_number, 6, "sent")  # 'approved' is column F

    print(f"Sent {len(queue)} items")


if __name__ == "__main__":
    main()
