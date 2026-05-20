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


def handle_booking(booker: CPDBooker, pending: dict, courts: List[Slot]) -> Optional[List[List[Slot]]]:
    """Try to book any of `courts` (same day+time, different court resources).
    Returns None on success. Returns fresh time-groups if none of the requested
    courts are still available."""
    saturday = datetime.fromisoformat(pending["saturday"])
    sunday = datetime.fromisoformat(pending["sunday"])
    run_id = pending["run_id"]
    first = courts[0]
    target_date = saturday if first.day_label == "Saturday" else sunday

    booker.open_target_day(target_date)

    sat_slots = booker.scrape_slots(saturday, "Saturday")
    sun_slots = booker.scrape_slots(sunday, "Sunday")
    current = sorted(sat_slots + sun_slots, key=lambda s: s.start)

    requested = [(c.resource_name, c.start) for c in courts]
    available_for_time = [
        s for s in current
        if (s.resource_name, s.start) in requested
    ]

    if not available_for_time:
        from app import filter_display_slots as _fds  # avoid name collision
        return group_slots_by_time(_fds(current))

    for slot in available_for_time:
        try:
            success = booker.book_slot(slot)
        except Exception as e:
            send_telegram(f"⚠️ Booking attempt on {slot.resource_name} crashed: {type(e).__name__} — trying next court.")
            success = False
        if not success:
            continue
        if not is_dry_run_enabled():
            set_weekend_booking_lock(saturday, sunday, slot, run_id)
            send_calendar_invite(slot)
        outcome = "Would book (dry run)" if is_dry_run_enabled() else "Booked"
        send_telegram(f"{outcome}: {format_slot(slot)}")
        return None

    # Tried every available court for this time and all failed.
    send_telegram(
        f"Failed to book {first.day_label} {first.start.strftime('%-I:%M %p')} — "
        f"tried {len(available_for_time)} court(s). Pick another time:"
    )
    from app import filter_display_slots as _fds2
    return group_slots_by_time(_fds2(current))


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

            # Normalize: accept either button callback_data or plain text reply.
            command = None
            if "callback_query" in update:
                cq = update["callback_query"]
                if str(cq["from"]["id"]) != chat_id:
                    continue
                _telegram_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
                command = cq.get("data", "").strip()
            elif "message" in update:
                msg = update["message"]
                if str(msg.get("from", {}).get("id", "")) != chat_id:
                    continue
                text = (msg.get("text") or "").strip()
                upper = text.upper()
                if upper in ("YES", "Y"):
                    command = "WEEK_YES"
                elif upper in ("NO",):
                    command = "WEEK_NO"
                elif upper == "N":
                    command = "SKIP"
                elif text.lower() == "refresh":
                    command = "REFRESH"
                elif text.isdigit():
                    command = f"SLOT_{int(text) - 1}"
                else:
                    continue
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

            # ── Slot actions ─────────────────────────────────────────────────
            pending = load_pending_choice()
            if not pending:
                send_telegram("That session has expired — run the booker again to see fresh slots.")
                continue

            saturday = datetime.fromisoformat(pending["saturday"])
            sunday = datetime.fromisoformat(pending["sunday"])

            if command == "SKIP":
                send_telegram("Skipped — nothing booked.")
                clear_pending_choice()
                continue

            if command == "REFRESH":
                send_telegram("Refreshing available times...")
                try:
                    with open_booker() as booker:
                        slots = refresh_slots(booker, saturday, sunday)
                        if slots:
                            groups = group_slots_by_time(filter_display_slots(slots))
                            save_pending_choice(pending["run_id"], saturday, sunday, groups)
                            send_slot_options(groups, "Updated available times:")
                        else:
                            send_telegram("No slots available anymore.")
                            clear_pending_choice()
                except Exception as e:
                    send_telegram(f"⚠️ Refresh failed: {type(e).__name__}: {str(e)[:300]}")
                continue

            if command.startswith("SLOT_"):
                try:
                    idx = int(command.split("_", 1)[1])
                except (ValueError, IndexError):
                    continue
                groups_raw = pending.get("time_groups") or []
                if not (0 <= idx < len(groups_raw)):
                    send_telegram("That option is no longer in the list — try refresh.")
                    continue
                courts = [slot_from_dict(d) for d in groups_raw[idx]]
                first = courts[0]
                send_telegram(
                    f"Booking {first.day_label} {first.start.strftime('%-I:%M %p')}… "
                    f"({len(courts)} court{'s' if len(courts) != 1 else ''} to try)"
                )
                try:
                    with open_booker() as booker:
                        leftover = handle_booking(booker, pending, courts)
                        if leftover is None:
                            clear_pending_choice()
                        elif leftover:
                            save_pending_choice(pending["run_id"], saturday, sunday, leftover)
                            send_slot_options(leftover, "Updated available times:")
                        else:
                            send_telegram("No other slots are available either.")
                            clear_pending_choice()
                except Exception as e:
                    send_telegram(
                        f"⚠️ Booking failed: {type(e).__name__}: {str(e)[:300]}\n"
                        f"Your slot list is still active — tap another time."
                    )
                continue


if __name__ == "__main__":
    run()
