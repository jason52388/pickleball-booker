"""
Persistent Telegram bot listener.
Handles slot selection replies from the user after the cron job exits.
Managed by systemd — restarts automatically on crash.
"""
import os
import time
from contextlib import contextmanager
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from app import (
    CPDBooker, Slot,
    _telegram_api, send_telegram, send_slot_options, filter_display_slots,
    load_pending_choice, save_pending_choice, clear_pending_choice,
    load_pending_weekend, clear_pending_weekend, set_weekend_decision,
    slot_from_dict, slot_to_dict, format_slot,
    send_calendar_invite, set_weekend_booking_lock,
    is_dry_run_enabled, upcoming_weekend,
)
from typing import Iterator, List, Optional


@contextmanager
def open_booker() -> Iterator[CPDBooker]:
    """Open a fresh logged-in browser for a single Telegram action.

    The VPS runs bot_listener.py continuously while manual/cron jobs may also
    start CPDBooker. Holding a persistent Chromium profile open between Telegram
    updates leaves SingletonLock in place and makes later daily_runner launches
    hang or time out. Keep the browser lifetime scoped to one action instead.
    """
    booker = CPDBooker()
    try:
        booker.__enter__()
        booker.login()
        yield booker
    finally:
        try:
            booker.__exit__(None, None, None)
        except Exception:
            pass


def refresh_slots(booker: CPDBooker, saturday: datetime, sunday: datetime) -> List[Slot]:
    booker.open_target_day(saturday)
    sat_slots = booker.scrape_slots(saturday, "Saturday")
    booker.open_target_day(sunday)
    sun_slots = booker.scrape_slots(sunday, "Sunday")
    return sorted(sat_slots + sun_slots, key=lambda s: s.start)


def handle_booking(booker: CPDBooker, pending: dict, picked: Slot) -> Optional[List[Slot]]:
    """Books the slot. Returns None on success, or a fresh slot list if the slot was gone."""
    saturday = datetime.fromisoformat(pending["saturday"])
    sunday = datetime.fromisoformat(pending["sunday"])
    run_id = pending["run_id"]
    target_date = saturday if picked.day_label == "Saturday" else sunday

    # ensure_booker() already called login() immediately before this function,
    # so reCAPTCHA has a fresh token from that recent navigation. No second
    # login() needed — a double goto() leaves the page mid-load and causes
    # open_target_day to time out on the date picker.
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

            # Accept plain text replies (number, N, refresh)
            if "message" in update:
                msg = update["message"]
                if str(msg.get("from", {}).get("id", "")) != chat_id:
                    continue
                text = msg.get("text", "").strip()
            elif "callback_query" in update:
                # Acknowledge and ignore stale button taps from old messages
                cq = update["callback_query"]
                _telegram_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
                continue
            else:
                continue

            # Weekend confirmation takes priority — Y/N for slot picking is "N"/digit,
            # but YES/NO (full words) means weekend confirm.
            upper = text.upper()
            pending_weekend = load_pending_weekend()
            if pending_weekend and upper in ("YES", "Y", "NO"):
                sat = datetime.fromisoformat(pending_weekend["saturday"])
                sun = datetime.fromisoformat(pending_weekend["sunday"])
                if upper in ("YES", "Y"):
                    set_weekend_decision(sat, sun, "confirmed")
                    send_telegram(
                        f"✅ Got it — I'll book for {sat.strftime('%a %b %-d')}/"
                        f"{sun.strftime('%a %b %-d')} on Sunday morning."
                    )
                else:
                    set_weekend_decision(sat, sun, "declined")
                    send_telegram(
                        f"👍 Skipping {sat.strftime('%a %b %-d')}/"
                        f"{sun.strftime('%a %b %-d')}. I'll ask again about the next weekend."
                    )
                clear_pending_weekend()
                continue

            pending = load_pending_choice()
            if not pending:
                send_telegram("That session has expired — run the booker again to see fresh slots.")
                continue

            saturday = datetime.fromisoformat(pending["saturday"])
            sunday = datetime.fromisoformat(pending["sunday"])

            if text.upper() == "N":
                send_telegram("Skipped — nothing booked.")
                clear_pending_choice()

            elif text.lower() == "refresh":
                send_telegram("Refreshing available times...")
                try:
                    with open_booker() as booker:
                        slots = refresh_slots(booker, saturday, sunday)
                        if slots:
                            display = filter_display_slots(slots)
                            save_pending_choice(pending["run_id"], saturday, sunday, display)
                            send_slot_options(display, "Updated available times:")
                        else:
                            send_telegram("No slots available anymore.")
                            clear_pending_choice()
                except Exception as e:
                    send_telegram(f"⚠️ Refresh failed: {type(e).__name__}: {str(e)[:300]}")

            elif text.isdigit():
                slots = [slot_from_dict(s) for s in pending["slots"]]
                idx = int(text) - 1
                if not (0 <= idx < len(slots)):
                    continue
                picked = slots[idx]
                send_telegram(f"Checking availability and booking {format_slot(picked)}...")
                try:
                    with open_booker() as booker:
                        leftover = handle_booking(booker, pending, picked)
                        if leftover is None:
                            clear_pending_choice()
                        elif leftover:
                            display = filter_display_slots(leftover)
                            save_pending_choice(pending["run_id"], saturday, sunday, display)
                            send_slot_options(display, "That slot is no longer available. Updated times:")
                        else:
                            send_telegram("That slot is gone and no other slots are available.")
                            clear_pending_choice()
                except Exception as e:
                    send_telegram(f"⚠️ Booking failed: {type(e).__name__}: {str(e)[:300]}\nYour slot list is still active — reply with another number or N.")


if __name__ == "__main__":
    run()
