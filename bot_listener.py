"""
Persistent Telegram bot listener.
Handles slot selection replies from the user after the cron job exits.
Managed by systemd — restarts automatically on crash.
"""
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from app import (
    CPDBooker, Slot,
    _telegram_api, send_telegram, send_slot_options, filter_display_slots,
    group_slots_by_time,
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


def _weekend_for(target_dt: datetime) -> (datetime, datetime):
    """Return (saturday, sunday) midnight datetimes for the weekend containing
    `target_dt`. Assumes target_dt's date is a Saturday or Sunday."""
    weekday = target_dt.weekday()
    if weekday == 5:  # Saturday
        sat = target_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        sun = sat + timedelta(days=1)
    elif weekday == 6:  # Sunday
        sun = target_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        sat = sun - timedelta(days=1)
    else:
        # Best-effort: nearest upcoming weekend
        days_until_sat = (5 - weekday) % 7
        sat = (target_dt + timedelta(days=days_until_sat)).replace(hour=0, minute=0, second=0, microsecond=0)
        sun = sat + timedelta(days=1)
    return sat, sun


def book_at_time(target_dt: datetime) -> None:
    """Self-contained booking attempt. Scrape the weekend that `target_dt`
    falls in, find every court available at that exact start time, try each
    until one books. If no courts are available at that time (or all fail),
    send a refreshed time list."""
    saturday, sunday = _weekend_for(target_dt)
    pretty = target_dt.strftime("%a %b %-d, %-I:%M %p")
    send_telegram(f"Trying to book {pretty}…")

    try:
        with open_booker() as booker:
            slots = refresh_slots(booker, saturday, sunday)
            target_courts = sorted(
                [s for s in slots if s.start == target_dt],
                key=lambda s: s.resource_name,
            )

            if not target_courts:
                groups = group_slots_by_time(filter_display_slots(slots))
                if groups:
                    send_slot_options(
                        groups,
                        f"{pretty} isn't available anymore. Pick from current options:",
                    )
                else:
                    send_telegram(f"{pretty} isn't available, and no other times are open either.")
                return

            for court in target_courts:
                try:
                    success = booker.book_slot(court)
                except Exception as e:
                    send_telegram(f"  {court.resource_name}: {type(e).__name__} — trying next court")
                    continue
                if not success:
                    continue
                if not is_dry_run_enabled():
                    set_weekend_booking_lock(saturday, sunday, court, "BOT")
                    send_calendar_invite(court)
                outcome = "Would book (dry run)" if is_dry_run_enabled() else "Booked"
                send_telegram(f"✅ {outcome}: {format_slot(court)}")
                return

            # Every court at this exact time failed.
            slots = refresh_slots(booker, saturday, sunday)
            groups = group_slots_by_time(filter_display_slots(slots))
            if groups:
                send_slot_options(
                    groups,
                    f"Tried {len(target_courts)} court(s) at {pretty} — none succeeded. Pick another:",
                )
            else:
                send_telegram(f"Tried every court at {pretty}; none worked, and no other times are open.")
    except Exception as e:
        send_telegram(f"⚠️ Booking failed: {type(e).__name__}: {str(e)[:300]}")


def refresh_for_weekend(saturday: datetime, sunday: datetime) -> None:
    send_telegram("Refreshing available times…")
    try:
        with open_booker() as booker:
            slots = refresh_slots(booker, saturday, sunday)
            groups = group_slots_by_time(filter_display_slots(slots))
            if groups:
                send_slot_options(groups, "Updated available times:")
            else:
                send_telegram("No times available for that weekend.")
    except Exception as e:
        send_telegram(f"⚠️ Refresh failed: {type(e).__name__}: {str(e)[:300]}")


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

            # Parse the update. Callbacks are self-contained: BOOK_<iso>,
            # REFRESH_<sat_date>, SKIP, WEEK_YES, WEEK_NO. Text replies are a
            # limited fallback (YES/NO for weekend confirmation only).
            command = None
            target_dt = None
            target_sat = None
            if "callback_query" in update:
                cq = update["callback_query"]
                if str(cq["from"]["id"]) != chat_id:
                    continue
                _telegram_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
                data = cq.get("data", "").strip()
                if data == "SKIP":
                    command = "SKIP"
                elif data in ("WEEK_YES", "WEEK_NO"):
                    command = data
                elif data.startswith("BOOK_"):
                    try:
                        target_dt = datetime.fromisoformat(data[5:])
                        command = "BOOK"
                    except ValueError:
                        continue
                elif data.startswith("REFRESH_"):
                    try:
                        target_sat = datetime.fromisoformat(data[8:])
                        command = "REFRESH"
                    except ValueError:
                        continue
                elif data.startswith("DECLINE_"):
                    try:
                        target_sat = datetime.fromisoformat(data[8:])
                        command = "DECLINE"
                    except ValueError:
                        continue
                else:
                    continue
            elif "message" in update:
                msg = update["message"]
                if str(msg.get("from", {}).get("id", "")) != chat_id:
                    continue
                text = (msg.get("text") or "").strip().upper()
                if text in ("YES", "Y"):
                    command = "WEEK_YES"
                elif text == "NO":
                    command = "WEEK_NO"
                else:
                    continue  # text replies only matter for YES/NO
            else:
                continue

            # ── Weekend confirmation ─────────────────────────────────────────
            if command in ("WEEK_YES", "WEEK_NO"):
                pending_weekend = load_pending_weekend()
                if not pending_weekend:
                    send_telegram("No pending weekend question — nothing to confirm.")
                    continue
                sat = datetime.fromisoformat(pending_weekend["saturday"])
                sun = datetime.fromisoformat(pending_weekend["sunday"])
                if command == "WEEK_YES":
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

            # ── Slot actions (self-contained callbacks; no pending state) ────
            if command == "SKIP":
                send_telegram("Skipped — nothing booked.")
                continue

            if command == "REFRESH":
                sun = target_sat + timedelta(days=1)
                refresh_for_weekend(target_sat, sun)
                continue

            if command == "DECLINE":
                sun = target_sat + timedelta(days=1)
                set_weekend_decision(target_sat, sun, "declined")
                send_telegram(
                    f"👍 Got it — won't book for {target_sat.strftime('%a %b %-d')}/"
                    f"{sun.strftime('%a %b %-d')}. You won't hear about this weekend again."
                )
                continue

            if command == "BOOK":
                book_at_time(target_dt)
                continue


if __name__ == "__main__":
    run()
