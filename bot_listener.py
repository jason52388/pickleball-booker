"""
Persistent Telegram bot listener.
Handles slot selection replies from the user after the cron job exits.
Managed by systemd — restarts automatically on crash.
"""
import os
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from app import (
    CPDBooker, Slot,
    _telegram_api, send_telegram, send_slot_options,
    load_pending_choice, save_pending_choice, clear_pending_choice,
    slot_from_dict, slot_to_dict, format_slot,
    send_calendar_invite, set_weekend_booking_lock,
    is_dry_run_enabled, upcoming_weekend,
)
from typing import List, Optional


def refresh_slots(saturday: datetime, sunday: datetime) -> List[Slot]:
    with CPDBooker() as booker:
        booker.login()
        booker.open_target_day(saturday)
        sat_slots = booker.scrape_slots(saturday, "Saturday")
        booker.open_target_day(sunday)
        sun_slots = booker.scrape_slots(sunday, "Sunday")
    return sorted(sat_slots + sun_slots, key=lambda s: s.start)


def handle_booking(pending: dict, picked: Slot) -> Optional[List[Slot]]:
    """Books the slot. Returns None on success, or a fresh slot list if the slot was gone."""
    saturday = datetime.fromisoformat(pending["saturday"])
    sunday = datetime.fromisoformat(pending["sunday"])
    run_id = pending["run_id"]
    target_date = saturday if picked.day_label == "Saturday" else sunday

    with CPDBooker() as booker:
        booker.login()
        booker.open_target_day(target_date)

        sat_slots = booker.scrape_slots(saturday, "Saturday")
        sun_slots = booker.scrape_slots(sunday, "Sunday")
        current = sorted(sat_slots + sun_slots, key=lambda s: s.start)

        still_available = any(
            s.resource_name == picked.resource_name and s.start == picked.start
            for s in current
        )

        if not still_available:
            return current

        success = booker.book_slot(picked)
        if success and not is_dry_run_enabled():
            set_weekend_booking_lock(saturday, sunday, picked, run_id)
            send_calendar_invite(picked)
        outcome = "Would book (dry run)" if is_dry_run_enabled() else ("Booked" if success else "Failed booking")
        send_telegram(f"{outcome}: {format_slot(picked)}")
        return None


def run() -> None:
    chat_id = str(os.getenv("TELEGRAM_CHAT_ID", ""))

    # Ignore all updates that arrived before we started
    resp = _telegram_api("getUpdates", {"limit": 1, "offset": -1})
    updates = resp.get("result", [])
    offset = (updates[-1]["update_id"] + 1) if updates else 0

    print("Bot listener started.", flush=True)

    while True:
        resp = _telegram_api("getUpdates", {"offset": offset, "timeout": 30})
        # If the API call failed (returned {}), back off to avoid hammering Telegram.
        if not resp.get("ok"):
            time.sleep(5)
            continue
        for update in resp.get("result", []):
            offset = update["update_id"] + 1

            if "callback_query" not in update:
                continue

            cq = update["callback_query"]
            if str(cq["from"]["id"]) != chat_id:
                continue

            _telegram_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
            data = cq["data"].strip()

            pending = load_pending_choice()
            if not pending:
                send_telegram("No active booking request.")
                continue

            saturday = datetime.fromisoformat(pending["saturday"])
            sunday = datetime.fromisoformat(pending["sunday"])

            if data == "N":
                send_telegram("Skipped — nothing booked.")
                clear_pending_choice()

            elif data == "\U0001f504 Refresh":
                send_telegram("Refreshing available times...")
                slots = refresh_slots(saturday, sunday)
                if slots:
                    pending["slots"] = [slot_to_dict(s) for s in slots]
                    save_pending_choice(pending["run_id"], saturday, sunday, slots)
                    send_slot_options(slots, "Updated available times — tap to book:")
                else:
                    send_telegram("No slots available anymore.")
                    clear_pending_choice()

            elif data.isdigit():
                slots = [slot_from_dict(s) for s in pending["slots"]]
                idx = int(data) - 1
                if not (0 <= idx < len(slots)):
                    continue
                picked = slots[idx]
                send_telegram(f"Checking availability and booking {format_slot(picked)}...")
                leftover = handle_booking(pending, picked)
                if leftover is None:
                    clear_pending_choice()
                elif leftover:
                    pending["slots"] = [slot_to_dict(s) for s in leftover]
                    save_pending_choice(pending["run_id"], saturday, sunday, leftover)
                    send_slot_options(leftover, "That slot is no longer available. Updated times:")
                else:
                    send_telegram("That slot is gone and no other slots are available.")
                    clear_pending_choice()


if __name__ == "__main__":
    run()
