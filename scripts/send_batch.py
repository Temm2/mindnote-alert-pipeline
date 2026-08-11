"""Reads unposted rows from a sheet tab, sends one formatted batch to
Telegram, and marks those rows as posted. Called with an argument:
    python send_batch.py b2b
    python send_batch.py events
"""
import sys
from datetime import datetime
from common import get_sheet, send_telegram

MAX_PER_BATCH = {"b2b": 15, "events": 5}
TAB_NAME = {"b2b": "b2b_log", "events": "events_log"}


def format_b2b_row(row: dict) -> str:
    return f"🔎 {row['company']} is looking for {row['seeking']} — {row['source_url']}"


def format_event_row(row: dict) -> str:
    when = row.get("when", "")
    try:
        when = datetime.fromisoformat(when.replace("Z", "+00:00")).strftime("%a %b %d, %H:%M UTC")
    except ValueError:
        pass
    return f"📅 {row['event_name']}\n{when}\n{row['link']}"


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("b2b", "events"):
        print("Usage: python send_batch.py [b2b|events]")
        sys.exit(1)

    kind = sys.argv[1]
    sheet = get_sheet(TAB_NAME[kind])
    records = sheet.get_all_records()

    unposted_rows = [(i + 2, r) for i, r in enumerate(records) if r.get("posted", "no") == "no"]
    batch = unposted_rows[: MAX_PER_BATCH[kind]]

    if not batch:
        print("Nothing to send")
        return

    formatter = format_b2b_row if kind == "b2b" else format_event_row
    message = "\n\n".join(formatter(row) for _, row in batch)
    send_telegram(message)

    for row_number, _ in batch:
        sheet.update_cell(row_number, 5, "yes")  # 'posted' is column E in both tabs

    print(f"Sent and marked {len(batch)} rows as posted")


if __name__ == "__main__":
    main()
