"""Reads un-sent lead rows from notetaker_leads and sends them to a
PRIVATE Telegram bot/chat for your team to review and manually reach
out to. Marks each sent row's 'contacted' cell as 'sent' (distinct
from the 'no'/'yes' values your team uses to track real outreach) so
the same leads aren't re-sent every run.
"""
import os
from common import get_sheet, send_telegram

MAX_PER_BATCH = 10


def format_lead_row(row: dict) -> str:
    return f"🎯 {row.get('company','')} — {row.get('seeking','')}\n{row.get('link','')}"


def main():
    sheet = get_sheet("notetaker_leads")
    header_row = sheet.row_values(1)
    header_lower = [h.strip().lower() for h in header_row]

    if "contacted" not in header_lower:
        print("Warning: no 'contacted' column found in notetaker_leads.")
        return
    contacted_col_index = header_lower.index("contacted") + 1

    all_values = sheet.get_all_values()[1:]  # skip header row
    unsent = []
    for i, row_values in enumerate(all_values):
        record = {header_lower[j]: (row_values[j] if j < len(row_values) else "")
                  for j in range(len(header_lower))}
        if record.get("contacted", "").strip().lower() == "no":
            unsent.append((i + 2, record))

    batch = unsent[:MAX_PER_BATCH]
    if not batch:
        print("Nothing to send")
        return

    message = "\n\n".join(format_lead_row(row) for _, row in batch)

    bot_token = os.environ.get("TELEGRAM_LEADS_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_LEADS_CHAT_ID")
    if not bot_token or not chat_id:
        print("TELEGRAM_LEADS_BOT_TOKEN or TELEGRAM_LEADS_CHAT_ID not set, cannot send.")
        return

    send_telegram(message, chat_id=chat_id, bot_token=bot_token)

    for row_number, _ in batch:
        sheet.update_cell(row_number, contacted_col_index, "sent")

    print(f"Sent {len(batch)} leads")


if __name__ == "__main__":
    main()
