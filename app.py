import json
import os
import random
import re
import smtplib
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import requests as http_requests

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


load_dotenv()

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
BOOKED_WEEKENDS_FILE = DATA_DIR / "booked_weekends.json"
PENDING_CHOICE_FILE = DATA_DIR / "pending_choice.json"


@dataclass
class Slot:
    day_label: str
    label: str
    start: datetime
    end: Optional[datetime]
    locator_hint: str
    resource_name: str
    row_index: int
    col_index: int



def parse_slot_time(time_label: str, target_date: datetime) -> Optional[Tuple[datetime, Optional[datetime]]]:
    match = re.search(r"(\d{1,2}:\d{2}\s*[APMapm]{2}).*?(\d{1,2}:\d{2}\s*[APMapm]{2})?", time_label)
    if not match:
        return None
    start_str = match.group(1)
    end_str = match.group(2)
    start = datetime.strptime(f"{target_date.strftime('%Y-%m-%d')} {start_str.upper()}", "%Y-%m-%d %I:%M %p")
    end = None
    if end_str:
        end = datetime.strptime(f"{target_date.strftime('%Y-%m-%d')} {end_str.upper()}", "%Y-%m-%d %I:%M %p")
    return start, end


def is_valid_pickleball_slot(label: str) -> bool:
    include = os.getenv("PICKLEBALL_INCLUDE", "pickleball").lower()
    exclude = os.getenv("PICKLEBALL_EXCLUDE", "ball machine").lower()
    facility = os.getenv("TARGET_FACILITY", "McFetridge").lower()
    normalized = label.lower()
    if include not in normalized or exclude in normalized or facility not in normalized:
        return False

    # Guardrail: only treat court-style resources as bookable pickleball courts.
    # Examples allowed: Pickleball1A, Pickleball 1B, Pickleball2B.
    # This avoids machine/equipment rows that can be truncated in the grid UI.
    if re.search(r"pickleball\s*\d+[a-z]?$", normalized):
        return True
    return False


def is_preferred_time(slot: Slot) -> bool:
    start_hour = int(os.getenv("PREFERRED_START_HOUR", "8"))
    end_hour = int(os.getenv("PREFERRED_END_HOUR", "11"))
    return start_hour <= slot.start.hour <= end_hour


def preferred_hours_display() -> str:
    """Human-readable preferred window (mirrors is_preferred_time / env)."""
    start_hour = int(os.getenv("PREFERRED_START_HOUR", "8"))
    end_hour = int(os.getenv("PREFERRED_END_HOUR", "11"))
    start_l = datetime(2000, 1, 1, start_hour).strftime("%-I:%M %p")
    end_l = datetime(2000, 1, 1, end_hour).strftime("%-I:%M %p")
    return f"{start_l}–{end_l}"


def _dry_run_poll_seconds() -> int:
    if os.getenv("DRY_RUN_POLL_SECONDS"):
        try:
            return max(5, int(os.getenv("DRY_RUN_POLL_SECONDS")))
        except ValueError:
            pass
    raw = (os.getenv("DRY_RUN_POLL_MINUTES") or "30").strip()
    try:
        return max(1, int(raw)) * 60
    except ValueError:
        return 30 * 60


def dry_run_poll_deadline_ct(now_ct: datetime) -> datetime:
    """End of polling window for dry-run (any day / any clock time)."""
    return now_ct + timedelta(seconds=_dry_run_poll_seconds())


def booking_search_window_description() -> str:
    """Text for emails; dry-run uses DRY_RUN_POLL_SECONDS/MINUTES, prod uses 7:00–7:10 CT."""
    if is_dry_run_enabled():
        secs = _dry_run_poll_seconds()
        return f"a dry-run polling window of up to {secs} seconds from run start"
    return "7:00–7:10 AM CT"


def _telegram_api(method: str, payload: dict) -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {}
    try:
        r = http_requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload,
            timeout=15,
        )
        return r.json()
    except Exception:
        return {}


def send_telegram_photo(photo_path: str, caption: str = "") -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        with open(photo_path, "rb") as f:
            http_requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": f},
                timeout=30,
            )
    except Exception:
        pass


def send_telegram(message: str, buttons: Optional[List[str]] = None) -> None:
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        return
    payload: dict = {"chat_id": chat_id, "text": message}
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": b, "callback_data": b}] for b in buttons]
        }
    _telegram_api("sendMessage", payload)


def read_json_file(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json_file(path: Path, payload) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def wait_for_telegram_choice(options_count: int, timeout: int = 600) -> Optional[str]:
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not chat_id or not token:
        return None

    # Ignore any updates that arrived before we started waiting
    resp = _telegram_api("getUpdates", {"limit": 1, "offset": -1})
    updates = resp.get("result", [])
    offset = (updates[-1]["update_id"] + 1) if updates else 0

    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = _telegram_api("getUpdates", {"offset": offset, "timeout": 10})
        if not resp.get("ok"):
            time.sleep(5)
            continue
        for update in resp.get("result", []):
            offset = update["update_id"] + 1

            # Button tap
            if "callback_query" in update:
                cq = update["callback_query"]
                if str(cq["from"]["id"]) == str(chat_id):
                    _telegram_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
                    data = cq["data"].strip().upper()
                    if data == "N":
                        return "N"
                    if data.isdigit() and 1 <= int(data) <= options_count:
                        return data

            # Plain text reply
            elif "message" in update:
                msg = update["message"]
                if str(msg["chat"]["id"]) == str(chat_id):
                    text = (msg.get("text") or "").strip().upper()
                    if text == "N":
                        return "N"
                    if text.isdigit() and 1 <= int(text) <= options_count:
                        return text

    return None


def slot_to_dict(slot: "Slot") -> dict:
    return {
        "day_label": slot.day_label,
        "label": slot.label,
        "start": slot.start.isoformat(),
        "end": slot.end.isoformat() if slot.end else None,
        "locator_hint": slot.locator_hint,
        "resource_name": slot.resource_name,
        "row_index": slot.row_index,
        "col_index": slot.col_index,
    }


def slot_from_dict(d: dict) -> "Slot":
    return Slot(
        day_label=d["day_label"],
        label=d["label"],
        start=datetime.fromisoformat(d["start"]),
        end=datetime.fromisoformat(d["end"]) if d.get("end") else None,
        locator_hint=d["locator_hint"],
        resource_name=d["resource_name"],
        row_index=d["row_index"],
        col_index=d["col_index"],
    )


def save_pending_choice(run_id: str, saturday: datetime, sunday: datetime, slots: List["Slot"]) -> None:
    write_json_file(PENDING_CHOICE_FILE, {
        "run_id": run_id,
        "saturday": saturday.isoformat(),
        "sunday": sunday.isoformat(),
        "slots": [slot_to_dict(s) for s in slots],
    })


def load_pending_choice() -> Optional[dict]:
    if not PENDING_CHOICE_FILE.exists():
        return None
    return read_json_file(PENDING_CHOICE_FILE, None)


def clear_pending_choice() -> None:
    PENDING_CHOICE_FILE.unlink(missing_ok=True)


def send_slot_options(slots: List["Slot"], header: str) -> None:
    lines = [header]
    buttons = []
    for idx, slot in enumerate(slots, start=1):
        lines.append(f"{idx}) {format_slot(slot)}")
        buttons.append(str(idx))
    buttons.extend(["\U0001f504 Refresh", "N"])
    send_telegram("\n".join(lines), buttons=buttons)


def is_dry_run_enabled() -> bool:
    return os.getenv("DRY_RUN", "false").lower() == "true"


def is_weekend_lock_enabled() -> bool:
    return os.getenv("BOOKING_LOCK_ENABLED", "true").lower() == "true"


def weekend_key(saturday: datetime, sunday: datetime) -> str:
    return f"{saturday.strftime('%Y-%m-%d')}__{sunday.strftime('%Y-%m-%d')}"


def has_weekend_booking_lock(saturday: datetime, sunday: datetime) -> bool:
    if not is_weekend_lock_enabled() or is_dry_run_enabled():
        return False
    payload = read_json_file(BOOKED_WEEKENDS_FILE, {})
    return weekend_key(saturday, sunday) in payload


def set_weekend_booking_lock(saturday: datetime, sunday: datetime, slot: Slot, run_id: str) -> None:
    if not is_weekend_lock_enabled() or is_dry_run_enabled():
        return
    payload = read_json_file(BOOKED_WEEKENDS_FILE, {})
    payload[weekend_key(saturday, sunday)] = {
        "run_id": run_id,
        "booked_at": datetime.utcnow().isoformat() + "Z",
        "slot": format_slot(slot),
    }
    write_json_file(BOOKED_WEEKENDS_FILE, payload)




class CPDBooker:
    def __init__(self):
        self.url = os.environ["CPD_URL"]
        self.username = os.environ["CPD_USERNAME"]
        self.password = os.environ["CPD_PASSWORD"]
        self.browser = None
        self.page = None

    def __enter__(self):
        self.pw = sync_playwright().start()
        headless = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
        profile_dir = str(DATA_DIR / "browser_profile")

        # Persistent context reuses cookies/session across runs so reCAPTCHA v3 sees a
        # returning user rather than a fresh bot session.
        launch_kwargs = dict(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )

        proxy_host = os.getenv("ISP_PROXY_HOST")
        proxy_port = os.getenv("ISP_PROXY_PORT")
        proxy_user = os.getenv("ISP_PROXY_USERNAME")
        proxy_pass = os.getenv("ISP_PROXY_PASSWORD")
        if all([proxy_host, proxy_port, proxy_user, proxy_pass]):
            launch_kwargs["proxy"] = {
                "server": f"http://{proxy_host}:{proxy_port}",
                "username": proxy_user,
                "password": proxy_pass,
            }
        try:
            self.context = self.pw.chromium.launch_persistent_context(
                profile_dir, channel="chrome", **launch_kwargs
            )
        except Exception:
            self.context = self.pw.chromium.launch_persistent_context(
                profile_dir, **launch_kwargs
            )
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self.browser = None  # not needed with persistent context
        self.page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            self.pw.stop()
        except Exception:
            pass

    def _is_logged_in(self) -> bool:
        # Wait for JS to settle before checking — avoids false negatives from
        # cached DOM showing "Sign In" briefly before session cookies are applied.
        self.page.wait_for_timeout(2000)
        try:
            self.page.get_by_role("link", name=re.compile(r"sign in|log in", re.I)).first.wait_for(
                state="visible", timeout=3000
            )
            return False
        except Exception:
            return True

    def login(self) -> None:
        print("[login] Loading landing page...", flush=True)
        for attempt in range(3):
            try:
                self.page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                break
            except Exception:
                if attempt == 2:
                    raise
                self.page.wait_for_timeout(3000)
        print("[login] Landing page loaded. Checking login state...", flush=True)
        if self._is_logged_in():
            print("[login] Already logged in. Reloading reservation page...", flush=True)
            try:
                self.page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self.page.wait_for_timeout(int(random.uniform(1500, 3000)))
            print("[login] Done (was already logged in).", flush=True)
            return
        print("[login] Not logged in. Dismissing modal...", flush=True)
        self._dismiss_modal()
        sign_in = self.page.locator("a:has-text('Sign In'), a:has-text('Sign in now')").first
        sign_in.wait_for(state="visible", timeout=10000)
        print("[login] Clicking Sign In...", flush=True)
        sign_in.click(timeout=8000, no_wait_after=True)
        try:
            self.page.wait_for_url("**/signin**", timeout=30000)
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        self.page.wait_for_timeout(1000)
        print("[login] On signin page. Filling credentials...", flush=True)
        email_selector = (
            "input[placeholder*='Email' i], input[aria-label*='Email' i], "
            "input[type='email'], input[name*='user'], input[id*='user'], input[id*='email']"
        )
        self.page.locator(email_selector).first.wait_for(state="visible", timeout=20000)
        self._fill_any(
            [
                ("label", r"email|username"),
                ("css", email_selector),
            ],
            self.username,
        )
        self._fill_any(
            [
                ("label", r"password"),
                ("css", "input[type='password'], input[aria-label*='Password' i]"),
            ],
            self.password,
        )
        print("[login] Submitting credentials...", flush=True)
        self._click_any(
            [
                ("role_button", r"sign in|log in"),
                ("css", "button[type='submit'], input[type='submit']"),
            ]
        )
        print("[login] Waiting for post-login page settle (up to 15s)...", flush=True)
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_timeout(int(random.uniform(2000, 4000)))
        print("[login] Navigating back to reservation page...", flush=True)
        try:
            self.page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        self.page.wait_for_timeout(int(random.uniform(1500, 3000)))
        print("[login] Login complete.", flush=True)

    def _dismiss_modal(self) -> None:
        """Force-remove any blocking modal overlay via JS."""
        try:
            self.page.evaluate("""
                document.querySelectorAll('.modal.is-open, [class*="error-modal"]').forEach(el => el.remove());
                document.body.classList.remove('modal-open', 'is-modal-open', 'has-modal');
                document.documentElement.classList.remove('modal-open', 'is-modal-open');
            """)
            self.page.wait_for_timeout(300)
        except Exception:
            pass

    def _click_any(self, selectors: Sequence[Tuple[str, str]]) -> None:
        last_error = None
        for kind, value in selectors:
            try:
                if kind == "role_link":
                    self.page.get_by_role("link", name=re.compile(value, re.I)).first.click(timeout=10000)
                elif kind == "role_button":
                    self.page.get_by_role("button", name=re.compile(value, re.I)).first.click(timeout=10000)
                elif kind == "css":
                    self.page.locator(value).first.click(timeout=10000)
                else:
                    continue
                return
            except Exception as exc:  # pragma: no cover - browser-dependent
                last_error = exc
        raise RuntimeError(f"Could not click any expected selector: {selectors}") from last_error

    def _fill_any(self, selectors: Sequence[Tuple[str, str]], value: str) -> None:
        last_error = None
        for kind, selector in selectors:
            try:
                if kind == "label":
                    locator = self.page.get_by_label(re.compile(selector, re.I)).first
                elif kind == "css":
                    locator = self.page.locator(selector).first
                else:
                    continue
                locator.fill(value, timeout=10000)
                return
            except Exception as exc:  # pragma: no cover - browser-dependent
                last_error = exc
        raise RuntimeError(f"Could not fill any expected selector: {selectors}") from last_error

    def open_target_day(self, target_date: datetime) -> None:
        # The date picker is a custom combobox (inputmode="none") — fill() is ignored.
        # Must click to open the calendar popup, navigate months, then click the target day.
        print(f"[open_target_day] Opening {target_date.strftime('%Y-%m-%d')}...", flush=True)
        self._dismiss_modal()
        date_input = self.page.get_by_label("Date picker, current date")
        date_input.click(timeout=10000)
        print("[open_target_day] Calendar opened.", flush=True)
        self.page.locator(".an-calendar").wait_for(timeout=5000)

        target_month = target_date.strftime("%B %Y")  # e.g. "May 2026"
        for _ in range(24):
            header = self.page.locator(".an-calendar-header-title").inner_text(timeout=3000).strip()
            if header == target_month:
                break
            header_date = datetime.strptime(header, "%B %Y")
            arrow = ".icon-chevron-right" if header_date < target_date else ".icon-chevron-left"
            self.page.locator(arrow).click()
            self.page.wait_for_timeout(400)

        print(f"[open_target_day] Clicking day {target_date.day}...", flush=True)
        day_str = str(target_date.day)
        day_cells = self.page.locator(".an-calendar-day:not(.an-calendar-day-othermonth)")
        count = day_cells.count()
        for i in range(count):
            cell = day_cells.nth(i)
            if cell.inner_text(timeout=1000).strip() == day_str:
                cell.click()
                break

        print("[open_target_day] Waiting for grid to settle...", flush=True)
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_timeout(3000)
        print("[open_target_day] Done.", flush=True)

    def scrape_slots(self, target_date: datetime, day_label: str) -> List[Slot]:
        extracted = self.page.evaluate(
            r"""
            () => {
              const table = document.querySelector('table');
              if (!table) return [];
              const rows = [...table.querySelectorAll('tr')];
              const out = [];
              rows.forEach((tr, rowIndex) => {
                const nameEl = tr.querySelector('.resource-header-cell__name');
                const nameText = (nameEl?.textContent || tr.querySelector('th')?.textContent || '').trim();
                const fullName = (nameEl?.getAttribute('title') || nameText || '').trim();
                if (!fullName) return;
                const cells = [...tr.querySelectorAll('td.td-grid-cell, td.grid-cell')];
                cells.forEach((td, colIndex) => {
                  // Use the inner grid-cell div as the source of truth.
                  // Available cells have no --disabled modifier and their aria-label ends with "Available".
                  const inner = td.querySelector('.grid-cell');
                  if (!inner) return;
                  const innerCls = (inner.className || '').toString();
                  if (innerCls.includes('disabled')) return;
                  const ariaLabel = (inner.getAttribute('aria-label') || '').trim();
                  if (!ariaLabel.toLowerCase().endsWith('available')) return;
                  // aria-label format: "{resource} {start} - {end} Available"
                  const timeMatch = ariaLabel.match(/(\d{1,2}:\d{2}\s*[APMapm]{2})\s*-\s*(\d{1,2}:\d{2}\s*[APMapm]{2})/);
                  const timeLabel = timeMatch ? timeMatch[0] : '';
                  out.push({
                    rowIndex,
                    colIndex,
                    resourceName: fullName,
                    timeLabel,
                  });
                });
              });
              return out;
            }
            """
        )
        candidates: List[Slot] = []
        for row in extracted:
            resource_name = row.get("resourceName", "")
            if not is_valid_pickleball_slot(resource_name):
                continue
            parsed_time = parse_slot_time(row.get("timeLabel", ""), target_date)
            if not parsed_time:
                continue
            start, end = parsed_time
            label = f"{resource_name} {row.get('timeLabel', '')}".strip()
            candidates.append(
                Slot(
                    day_label=day_label,
                    label=label,
                    start=start,
                    end=end,
                    locator_hint=resource_name[:120],
                    resource_name=resource_name,
                    row_index=int(row.get("rowIndex", -1)),
                    col_index=int(row.get("colIndex", -1)),
                )
            )
        unique = {(c.resource_name, c.start.isoformat()): c for c in candidates}
        return sorted(unique.values(), key=lambda s: s.start)

    def _human_pause(self, lo: float = 0.8, hi: float = 2.0) -> None:
        self.page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))

    def book_slot(self, slot: Slot) -> bool:
        if is_dry_run_enabled():
            return True
        try:
            row = self.page.locator("tr").nth(slot.row_index)
            cell = row.locator("td.td-grid-cell, td.grid-cell").nth(slot.col_index)
            cell.scroll_into_view_if_needed(timeout=2500)
            self._human_pause(0.5, 1.2)
            cell.click(timeout=2500)
            self._human_pause(1.5, 3.0)

            # Fill in the required Event name field before confirming
            event_name = os.getenv("BOOKING_EVENT_NAME", "Pickleball")
            try:
                name_input = self.page.get_by_label(re.compile("event name", re.I)).first
                name_input.click(timeout=10000)
                self._human_pause(0.3, 0.7)
                # Type character by character like a human
                for ch in event_name:
                    name_input.type(ch, delay=random.randint(60, 160))
            except Exception:
                pass

            self._human_pause(0.8, 1.8)

            # Click "Confirm bookings" — must match "confirm" to avoid "Clear all bookings"
            self._click_any(
                [
                    ("role_button", r"confirm bookings?"),
                    ("css", "button[class*='confirm']"),
                ]
            )
            self._human_pause(2.0, 4.0)

            # Handle disclaimers dialog if it appears
            try:
                checkbox = self.page.locator("input[type='checkbox']").first
                checkbox.wait_for(timeout=4000)
                if not checkbox.is_checked():
                    self._human_pause(0.5, 1.0)
                    checkbox.check()
                self._human_pause(0.5, 1.2)
                self._click_any([("role_button", r"save"), ("css", "button[class*='save']")])
                self._human_pause(1.5, 3.0)
            except Exception:
                pass

            # Final step: click Reserve to commit the booking
            self._human_pause(1.0, 2.0)
            self._click_any(
                [
                    ("role_button", r"^reserve$"),
                    ("css", "button.booking-detail__btn--continue"),
                ]
            )
            try:
                self.page.wait_for_load_state("networkidle", timeout=60000)
            except Exception:
                pass
            self._human_pause(2.0, 4.0)

            # Preview mode: screenshot the payment page and send to Telegram, then stop
            if os.getenv("PREVIEW_STOP_BEFORE_PAY", "false").lower() == "true":
                screenshot_path = str(DATA_DIR / "preview_payment.png")
                self.page.screenshot(path=screenshot_path, full_page=True)
                send_telegram_photo(screenshot_path, "Reached payment page — not paying (preview mode)")
                return True

            # Checkout page — accept any waiver checkbox, fill CVV inside the payment iframe, then pay
            cvv = os.getenv("CREDIT_CARD_CVV", "")
            if cvv:
                try:
                    # Accept waiver checkbox if present on the main page
                    waiver = self.page.locator("input[type='checkbox']").first
                    if waiver.is_visible(timeout=2000) and not waiver.is_checked():
                        waiver.check()
                    self.page.wait_for_timeout(500)
                except Exception:
                    pass

                try:
                    # CVV lives inside a cross-origin payment iframe
                    payment_frame = self.page.frame_locator("iframe[src*='checkoutcui.active.com']")
                    cvv_input = payment_frame.locator("input[id*='cvv']")
                    cvv_input.fill(cvv, timeout=5000)
                    # Pay button is on the main page (Order Summary sidebar)
                    self._click_any([("role_button", r"^pay$"), ("css", "button[class*='pay']")])
                    try:
                        self.page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    self.page.wait_for_timeout(2000)
                except Exception:
                    pass

            return True
        except (PlaywrightTimeoutError, RuntimeError, Exception):
            return False


def format_slot(slot: Slot) -> str:
    end_piece = f" - {slot.end.strftime('%I:%M %p')}" if slot.end else ""
    return f"{slot.day_label} {slot.start.strftime('%I:%M %p')}{end_piece} ({slot.resource_name})"


def upcoming_weekend(reference: datetime) -> Tuple[datetime, datetime]:
    # Monday=0 ... Sunday=6
    days_until_sat = (5 - reference.weekday()) % 7
    saturday = (reference + timedelta(days=days_until_sat)).replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = saturday + timedelta(days=1)
    return saturday, sunday


def choose_auto_book_slot(saturday_slots: List[Slot], sunday_slots: List[Slot]) -> Optional[Slot]:
    sat_pref = [s for s in saturday_slots if is_preferred_time(s)]
    sun_pref = [s for s in sunday_slots if is_preferred_time(s)]
    if sat_pref and sun_pref:
        return sat_pref[0]
    if sat_pref:
        return sat_pref[0]
    if sun_pref:
        return sun_pref[0]
    return None


def write_last_run_context(run_id: str) -> None:
    write_json_file(DATA_DIR / "runtime_state.json", {"last_run_id": run_id, "updated_at": datetime.utcnow().isoformat() + "Z"})


def is_cron_mode() -> bool:
    return os.getenv("CRON_MODE", "false").lower() == "true"


_GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
_GCAL_TOKEN = DATA_DIR / "gcal_token.json"
_GCAL_CLIENT_SECRET = DATA_DIR / "gcal_client_secret.json"


def _get_gcal_service():
    creds = None
    if _GCAL_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(_GCAL_TOKEN), _GCAL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(_GCAL_TOKEN, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                "Google Calendar not authorized. Run setup_gcal_auth.py first."
            )
    return build("calendar", "v3", credentials=creds)


def send_calendar_invite(slot: Slot) -> None:
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    invite_emails_raw = os.getenv("CALENDAR_INVITE_EMAILS", "")
    if not invite_emails_raw:
        print("send_calendar_invite: no recipients configured, skipping")
        return
    recipients = [e.strip() for e in invite_emails_raw.split(",") if e.strip()]

    chicago = ZoneInfo("America/Chicago")
    start_dt = slot.start.replace(tzinfo=chicago)
    end_dt = (slot.end or slot.start + timedelta(hours=1)).replace(tzinfo=chicago)

    # Google Calendar invite
    try:
        event = {
            "summary": f"Pickleball @ McFetridge ({slot.resource_name})",
            "location": "McFetridge Sports Center, 3843 N California Ave, Chicago, IL 60618",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Chicago"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Chicago"},
            "attendees": [{"email": r} for r in recipients],
            "reminders": {"useDefault": True},
        }
        service = _get_gcal_service()
        service.events().insert(
            calendarId="primary",
            body=event,
            sendUpdates="all",
        ).execute()
        print(f"send_calendar_invite: Google Calendar event created for {recipients}")
    except Exception as e:
        print(f"send_calendar_invite: Google Calendar failed — {e}")

    # Confirmation email
    if not all([smtp_user, smtp_password]):
        print("send_calendar_invite: no SMTP credentials, skipping email")
        return
    try:
        time_str = start_dt.strftime("%-I:%M %p")
        subject = f"Pickleball booked — {start_dt.strftime('%A, %B %-d')} at {time_str}"
        body = (
            f"Your pickleball court is booked!\n\n"
            f"Court: {slot.resource_name}\n"
            f"Date: {start_dt.strftime('%A, %B %-d, %Y')}\n"
            f"Time: {time_str} — {end_dt.strftime('%-I:%M %p')}\n"
            f"Location: McFetridge Sports Center, 3843 N California Ave, Chicago, IL 60618\n"
        )
        msg = MIMEMultipart("mixed")
        msg["From"] = smtp_user
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        print(f"send_calendar_invite: confirmation email sent to {recipients}")
    except Exception as e:
        print(f"send_calendar_invite: email failed — {e}")


def send_no_availability_email(saturday: datetime, sunday: datetime) -> None:
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    invite_emails_raw = os.getenv("CALENDAR_INVITE_EMAILS", "")
    if not all([smtp_user, smtp_password, invite_emails_raw]):
        return
    recipients = [e.strip() for e in invite_emails_raw.split(",") if e.strip()]
    subject = (
        f"No pickleball slots found — weekend of {saturday.strftime('%B %-d')}"
    )
    pref = preferred_hours_display()
    window = booking_search_window_description()
    body = (
        f"The pickleball booker checked during {window} and found no preferred "
        f"({pref}) slots available at McFetridge for the weekend of "
        f"{saturday.strftime('%A, %B %-d')} – {sunday.strftime('%A, %B %-d, %Y')}.\n\n"
        f"You may want to check manually at:\n"
        f"https://anc.apm.activecommunities.com/chicagoparkdistrict/reservation/landing/quick?groupId=2\n"
    )
    try:
        msg = MIMEMultipart("mixed")
        msg["From"] = smtp_user
        msg["To"] = recipients[0]
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    except Exception as e:
        print(f"send_no_availability_email: failed — {e}")


def main() -> None:
    run_id = uuid.uuid4().hex[:8].upper()
    write_last_run_context(run_id)

    chicago = ZoneInfo("America/Chicago")
    now_ct = datetime.now(chicago)
    # Sunday=6 days before Saturday, Monday=5 days before Saturday.
    # In dry-run mode always notify so testing works any day (prod only emails Su/Mo).
    send_no_avail_notification = is_dry_run_enabled() or now_ct.weekday() in (6, 0)
    if is_dry_run_enabled():
        # Dry-run: poll for DRY_RUN_POLL_MINUTES so VPS/GitHub-triggered runs work any day/time.
        deadline_ct = dry_run_poll_deadline_ct(now_ct)
    else:
        deadline_ct = now_ct.replace(hour=7, minute=10, second=0, microsecond=0)

    saturday, sunday = upcoming_weekend(now_ct)

    if has_weekend_booking_lock(saturday, sunday):
        return

    booked = False
    last_all_slots: List[Slot] = []

    with CPDBooker() as booker:
        booker.login()

        while datetime.now(chicago) < deadline_ct:
            booker.open_target_day(saturday)
            saturday_slots = booker.scrape_slots(saturday, "Saturday")
            booker.open_target_day(sunday)
            sunday_slots = booker.scrape_slots(sunday, "Sunday")
            last_all_slots = sorted(saturday_slots + sunday_slots, key=lambda s: s.start)
            print(f"[main] Found {len(saturday_slots)} Saturday slots, {len(sunday_slots)} Sunday slots ({len(last_all_slots)} total).", flush=True)

            picked_auto = choose_auto_book_slot(saturday_slots, sunday_slots)
            if picked_auto:
                target_date = saturday if picked_auto.day_label == "Saturday" else sunday
                booker.open_target_day(target_date)
                success = booker.book_slot(picked_auto)
                if success and not is_dry_run_enabled():
                    set_weekend_booking_lock(saturday, sunday, picked_auto, run_id)
                    send_calendar_invite(picked_auto)
                outcome = "Would book (dry run)" if is_dry_run_enabled() else ("Booked" if success else "Failed booking")
                send_telegram(f"{outcome}: {format_slot(picked_auto)}")
                booked = True
                break

            time.sleep(5)

    if not booked and send_no_avail_notification:
        send_no_availability_email(saturday, sunday)
        if not last_all_slots:
            send_telegram(
                f"No pickleball slots found at all for "
                f"{saturday.strftime('%b %-d')}–{sunday.strftime('%b %-d')}."
            )
        else:
            save_pending_choice(run_id, saturday, sunday, last_all_slots)
            send_slot_options(
                last_all_slots,
                f"No preferred {preferred_hours_display()} slot found. Tap a number to book a fallback, or N to skip:",
            )


if __name__ == "__main__":
    main()
