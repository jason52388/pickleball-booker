import json
import os
import random
import re
import smtplib
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
WEEKEND_DECISIONS_FILE = DATA_DIR / "weekend_decisions.json"
PENDING_WEEKEND_FILE = DATA_DIR / "pending_weekend.json"


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
    # Capture the start time, and optionally an end time after a "-" separator.
    # The end group must be anchored to the separator: a lazy ".*?" before an
    # optional group always matched empty, so the end time was silently dropped
    # and every slot looked like it had no end (breaking calendar-invite
    # durations, which then defaulted to a flat 1 hour).
    match = re.search(
        r"(\d{1,2}:\d{2}\s*[APMapm]{2})(?:\s*-\s*(\d{1,2}:\d{2}\s*[APMapm]{2}))?",
        time_label,
    )
    if not match:
        return None
    start_str = match.group(1)
    end_str = match.group(2)
    start = datetime.strptime(f"{target_date.strftime('%Y-%m-%d')} {start_str.upper()}", "%Y-%m-%d %I:%M %p")
    end = None
    if end_str:
        end = datetime.strptime(f"{target_date.strftime('%Y-%m-%d')} {end_str.upper()}", "%Y-%m-%d %I:%M %p")
    return start, end


def parse_calendar_header(header_text: str) -> datetime:
    """Parse the month-picker header to a naive datetime (first of month).

    The widget renders abbreviated month names ("Jun 2026"), but accept full
    names ("June 2026") too in case the rendering ever changes."""
    for fmt in ("%b %Y", "%B %Y"):
        try:
            return datetime.strptime(header_text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized calendar header: {header_text!r}")


def calendar_nav_arrow(header_text: str, target_date: datetime) -> str:
    """Chevron selector to step the month picker toward `target_date`.

    The header parses to a naive datetime; `target_date` may be tz-aware (it
    flows from datetime.now(chicago)), so compare tz-naively to avoid a
    naive/aware TypeError when navigating across a month boundary."""
    header_date = parse_calendar_header(header_text)
    target_naive = target_date.replace(tzinfo=None)
    return ".icon-chevron-right" if header_date < target_naive else ".icon-chevron-left"


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
    # For long-poll getUpdates the payload carries a server-side `timeout`
    # (seconds Telegram holds the connection open). The HTTP read timeout must
    # exceed it, or requests aborts the poll before Telegram replies.
    poll_timeout = payload.get("timeout", 0)
    http_timeout = poll_timeout + 15 if poll_timeout else 15
    try:
        r = http_requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload,
            timeout=http_timeout,
        )
        return r.json()
    except Exception:
        return {}


def send_telegram_photo(photo_path: str, caption: str = "") -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print(f"[telegram_photo] skipped — no token/chat_id configured", flush=True)
        return
    try:
        size = os.path.getsize(photo_path)
        print(f"[telegram_photo] sending {photo_path} ({size} bytes) caption='{caption[:60]}'", flush=True)
        with open(photo_path, "rb") as f:
            r = http_requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": f},
                timeout=30,
            )
        body = r.text[:400] if hasattr(r, "text") else ""
        print(f"[telegram_photo] status={r.status_code} body={body}", flush=True)
        # Telegram rejects photos >10MB or >10000px on a side. Fall back to a
        # document upload so the failure case still gives us something to look at.
        if r.status_code >= 400:
            with open(photo_path, "rb") as f:
                rd = http_requests.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": chat_id, "caption": caption},
                    files={"document": f},
                    timeout=60,
                )
            print(f"[telegram_photo] sendDocument fallback status={rd.status_code} body={rd.text[:400]}", flush=True)
    except Exception as e:
        print(f"[telegram_photo] EXCEPTION: {type(e).__name__}: {e}", flush=True)


def send_telegram(
    message: str,
    buttons: Optional[List[str]] = None,
    inline_keyboard: Optional[List[List[Tuple[str, str]]]] = None,
) -> None:
    """Send a Telegram message. `inline_keyboard` is a 2D layout of (label,
    callback_data) tuples; `buttons` is a legacy flat list (one per row)."""
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        return
    payload: dict = {"chat_id": chat_id, "text": message}
    if inline_keyboard:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": label, "callback_data": data} for (label, data) in row]
                for row in inline_keyboard
            ]
        }
    elif buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": b, "callback_data": b}] for b in buttons]
        }
    _telegram_api("sendMessage", payload)


def read_json_file(path: Path, default):
    if not path.exists():
        return default
    # A crash (or the SIGALRM kill switch) mid-write can leave a truncated file.
    # Treat unreadable/corrupt state as "no state" rather than crashing every
    # subsequent run.
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[state] {path.name} unreadable ({type(e).__name__}) — using default", flush=True)
        return default


def write_json_file(path: Path, payload) -> None:
    # Write to a temp file in the same directory, then atomically replace. This
    # guarantees readers never see a half-written file even if we're killed
    # mid-write (25-min SIGALRM kill switch / crash).
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


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


def save_pending_choice(run_id: str, saturday: datetime, sunday: datetime, time_groups: List[List["Slot"]]) -> None:
    """`time_groups` is a list of lists — each inner list holds Slots that share
    the same day + start time but cover different courts."""
    write_json_file(PENDING_CHOICE_FILE, {
        "run_id": run_id,
        "saturday": saturday.isoformat(),
        "sunday": sunday.isoformat(),
        "time_groups": [[slot_to_dict(s) for s in group] for group in time_groups],
    })


def load_pending_choice() -> Optional[dict]:
    if not PENDING_CHOICE_FILE.exists():
        return None
    return read_json_file(PENDING_CHOICE_FILE, None)


def clear_pending_choice() -> None:
    PENDING_CHOICE_FILE.unlink(missing_ok=True)


def filter_display_slots(slots: List["Slot"]) -> List["Slot"]:
    """Remove slots outside 7 AM – 7 PM. Falls back to all slots if the filter
    leaves nothing so we never silently drop every option."""
    filtered = [s for s in slots if 7 <= s.start.hour < 19]
    return filtered if filtered else slots


def group_slots_by_time(slots: List["Slot"]) -> List[List["Slot"]]:
    """Bucket slots by (day, start time); each bucket is sorted by court name
    so the booker tries them in a stable order. Buckets returned sorted by start."""
    buckets: dict = {}
    for s in slots:
        key = (s.day_label, s.start)
        buckets.setdefault(key, []).append(s)
    out = []
    for key in sorted(buckets.keys(), key=lambda k: k[1]):
        group = sorted(buckets[key], key=lambda s: s.resource_name)
        out.append(group)
    return out


def _time_group_label(group: List["Slot"]) -> str:
    first = group[0]
    return f"{first.day_label[:3]} {first.start.strftime('%-I:%M %p')}"


def send_slot_options(time_groups: List[List["Slot"]], header: str, run_id: str = "") -> None:
    """Send the time-group list as a Telegram inline keyboard. Each callback
    is self-contained — the slot's ISO timestamp is encoded into the button's
    callback_data, so tapping any button (even from an older message) is
    enough to know exactly what to book. No reliance on persisted state.

    The `run_id` parameter is accepted for backwards compatibility but is
    intentionally not embedded in callbacks anymore."""
    rows: List[List[Tuple[str, str]]] = []
    row: List[Tuple[str, str]] = []
    saturday_iso = ""
    for group in time_groups:
        first = group[0]
        # Track the Saturday of this weekend so REFRESH knows which weekend to
        # rescrape; derived from any group since they're all in the same weekend.
        if not saturday_iso:
            weekday = first.start.weekday()
            if weekday == 5:  # Saturday itself
                sat_date = first.start.date()
            else:  # Sunday
                sat_date = (first.start - timedelta(days=1)).date()
            saturday_iso = sat_date.isoformat()
        callback = f"BOOK_{first.start.isoformat()}"
        row.append((_time_group_label(group), callback))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        ("🔄 Refresh", f"REFRESH_{saturday_iso}"),
        ("❌ Dismiss", "SKIP"),
    ])
    rows.append([
        ("⏸ Don't book this weekend", f"DECLINE_{saturday_iso}"),
    ])
    send_telegram(header, inline_keyboard=rows)


def is_dry_run_enabled() -> bool:
    return os.getenv("DRY_RUN", "false").lower() == "true"


def is_preview_mode() -> bool:
    return os.getenv("PREVIEW_STOP_BEFORE_PAY", "false").lower() == "true"


def is_test_run() -> bool:
    """True for any run that should not send emails or write booking locks."""
    return is_dry_run_enabled() or is_preview_mode()


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
        "booked_at": datetime.now(timezone.utc).isoformat(),
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
        #
        # ignore_default_args strips Playwright defaults that reCAPTCHA Enterprise
        # reads:
        #   --enable-automation sets the "controlled by automated test software"
        #     infobar flag and exposes signals beyond what
        #     --disable-blink-features=AutomationControlled patches.
        #   --enable-features=... with the Playwright-injected feature flags
        #     differs from a real Chrome's enabled-features set; trimming it lets
        #     Chrome use its own defaults.
        # On a real desktop machine with a real GPU and residential IP, the
        # authentic browser fingerprint is *better* than any spoof. UA and WebGL
        # overrides are only useful on a server (Linux VPS + SwiftShader + no
        # plugins) where the real fingerprint screams "headless." Toggle via
        # BROWSER_FINGERPRINT_SPOOF — defaults to off so local runs don't break
        # things by mismatching their own OS.
        spoof_fingerprint = os.getenv("BROWSER_FINGERPRINT_SPOOF", "false").lower() == "true"

        launch_kwargs = dict(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
                "--no-first-run",
            ],
            ignore_default_args=[
                "--enable-automation",
            ],
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        if spoof_fingerprint:
            launch_kwargs["user_agent"] = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
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
        # Kill any stale Chrome process holding the profile lock so we don't
        # fall back to bundled Chromium unnecessarily.
        singleton = Path(profile_dir) / "SingletonLock"
        if singleton.exists():
            import signal as _signal
            try:
                target = os.readlink(str(singleton))  # "<hostname>-<pid>"
                stale_pid = int(target.split("-")[-1])
                os.kill(stale_pid, _signal.SIGTERM)
                import time as _time; _time.sleep(1)
            except Exception:
                pass
            try:
                singleton.unlink()
            except Exception:
                pass

        browser_channel = (os.getenv("BROWSER_CHANNEL") or "chrome").strip()
        try:
            print(f"[browser] launching channel={browser_channel!r} headless={headless}", flush=True)
            self.context = self.pw.chromium.launch_persistent_context(
                profile_dir, channel=browser_channel, **launch_kwargs
            )
            print(f"[browser] launched channel={browser_channel!r}", flush=True)
        except Exception as e:
            print(f"[browser] channel={browser_channel!r} failed: {type(e).__name__}: {e}", flush=True)
            print("[browser] falling back to bundled chromium", flush=True)
            self.context = self.pw.chromium.launch_persistent_context(
                profile_dir, **launch_kwargs
            )
            print("[browser] launched bundled chromium", flush=True)
        # Fingerprint patches. reCAPTCHA Enterprise sniffs these directly:
        #   navigator.webdriver       — must be undefined, not true
        #   navigator.plugins.length  — headless Chrome has 0; real Chrome has >0
        #   navigator.languages       — must match Accept-Language and be plural
        #   window.chrome             — must exist and have runtime/loadTimes
        #   permissions.query         — automation sometimes returns "denied" by
        #                              default for Notifications; real Chrome
        #                              returns "default" until the user chooses
        #   WebGL renderer            — SwiftShader on a GPU-less VPS leaks
        #                              "Google SwiftShader"; spoof to a common
        #                              Intel/Mesa string so it doesn't scream
        #                              "headless server"
        # Always-safe patches: these only HIDE automation tells. They don't
        # invent hardware that isn't there, so they're correct on every host.
        self.context.add_init_script(
            r"""
            // Hide webdriver
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

            // Plural languages (matches Accept-Language)
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});

            // window.chrome must look populated — automation sometimes leaves
            // these undefined.
            if (!window.chrome) { window.chrome = {}; }
            window.chrome.runtime = window.chrome.runtime || {};
            window.chrome.loadTimes = window.chrome.loadTimes || function () { return {}; };
            window.chrome.csi = window.chrome.csi || function () { return {}; };

            // permissions.query — real Chrome returns "prompt" for Notification
            // before any user choice; automation often defaults to "denied".
            const origQuery = (navigator.permissions && navigator.permissions.query) ? navigator.permissions.query.bind(navigator.permissions) : null;
            if (origQuery) {
                navigator.permissions.query = (params) => {
                    if (params && params.name === 'notifications') {
                        return Promise.resolve({state: Notification.permission === 'default' ? 'prompt' : Notification.permission});
                    }
                    return origQuery(params);
                };
            }
            """
        )

        # Spoof patches: only safe to apply when the real fingerprint would
        # otherwise scream "headless server" (no plugins, SwiftShader). On a
        # real desktop these would mismatch the genuine hardware/OS and look
        # WORSE. Enable with BROWSER_FINGERPRINT_SPOOF=true on a VPS.
        if spoof_fingerprint:
            self.context.add_init_script(
                r"""
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                        {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: ''},
                        {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: ''},
                    ],
                });

                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function (parameter) {
                    if (parameter === 37445) return 'Intel Inc.';
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                    return getParameter.apply(this, [parameter]);
                };
                if (window.WebGL2RenderingContext) {
                    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
                    WebGL2RenderingContext.prototype.getParameter = function (parameter) {
                        if (parameter === 37445) return 'Intel Inc.';
                        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                        return getParameter2.apply(this, [parameter]);
                    };
                }
                """
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

    def captcha_visible(self) -> bool:
        """Return True only if an *actual* captcha challenge is blocking the page.

        reCAPTCHA v3 ships a floating badge iframe (api2/anchor) on every protected
        page — that is not a challenge. The challenge popup uses api2/bframe.
        Matching plain `recaptcha` here would false-positive on the badge.
        """
        selectors = [
            'iframe[src*="recaptcha/api2/bframe"]',
            'iframe[src*="hcaptcha.com"][src*="challenge"]',
            'iframe[src*="turnstile"]',
            'div:has-text("Verify you are human")',
            'div:has-text("I\'m not a robot")',
        ]
        for sel in selectors:
            try:
                if self.page.locator(sel).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    def log_fingerprint_diagnostics(self) -> None:
        """Dump the patched fingerprint properties so we can confirm the init
        script actually applied. If reCAPTCHA is still failing, this rules out
        'the patches never landed' as a cause.
        """
        try:
            data = self.page.evaluate(
                """
                () => ({
                    webdriver: navigator.webdriver,
                    plugins_len: (navigator.plugins || {length: -1}).length,
                    languages: navigator.languages,
                    chrome_present: typeof window.chrome,
                    chrome_runtime: !!(window.chrome && window.chrome.runtime),
                    ua: navigator.userAgent,
                    platform: navigator.platform,
                    hw_concurrency: navigator.hardwareConcurrency,
                    webgl_vendor: (() => {
                        try {
                            const c = document.createElement('canvas').getContext('webgl');
                            return c ? c.getParameter(37445) : 'no-webgl';
                        } catch (e) { return 'err:' + e.message; }
                    })(),
                    webgl_renderer: (() => {
                        try {
                            const c = document.createElement('canvas').getContext('webgl');
                            return c ? c.getParameter(37446) : 'no-webgl';
                        } catch (e) { return 'err:' + e.message; }
                    })(),
                })
                """
            )
            print(f"[fingerprint] {json.dumps(data)}", flush=True)
        except Exception as e:
            print(f"[fingerprint] eval failed: {type(e).__name__}: {e}", flush=True)

    def clear_cart(self) -> None:
        """Empty any leftover items in the My Cart before starting a booking.

        Stale cart items survive across runs in the persistent profile. They
        inflate the total (we pay $40 instead of $20), and showing up at
        Reserve with a multi-item cart you didn't build this session is
        itself a bot-shaped pattern.
        """
        try:
            cart_link = self.page.get_by_role("link", name=re.compile(r"my cart", re.I)).first
            if not cart_link.is_visible(timeout=2000):
                return
            # Only bother visiting the cart if the badge shows items.
            badge_text = ""
            try:
                badge_text = cart_link.inner_text(timeout=1000)
            except Exception:
                pass
            # Match a digit > 0 in the badge ('My Cart 2', 'My Cart (2)', etc.)
            if not re.search(r"[1-9]", badge_text or ""):
                return
            print(f"[cart] stale items detected ({badge_text!r}) — clearing", flush=True)
            self._human_mouse_to(cart_link)
            cart_link.click(timeout=5000)
            self.page.wait_for_load_state("domcontentloaded", timeout=10000)
            self._human_idle(1.0, 2.0)
            # Common "remove from cart" affordances on Active Network sites.
            for _ in range(8):
                remove = self.page.locator(
                    "a:has-text('Remove'), button:has-text('Remove'), "
                    "a:has-text('Empty Cart'), button:has-text('Empty Cart')"
                ).first
                try:
                    if not remove.is_visible(timeout=1500):
                        break
                    self._human_mouse_to(remove)
                    remove.click(timeout=3000)
                    self._human_idle(0.8, 1.5)
                    # Some sites pop a confirmation modal.
                    try:
                        confirm = self.page.get_by_role(
                            "button", name=re.compile(r"^(yes|confirm|ok)$", re.I)
                        ).first
                        if confirm.is_visible(timeout=1500):
                            self._human_mouse_to(confirm)
                            confirm.click(timeout=2000)
                            self._human_idle(0.5, 1.2)
                    except Exception:
                        pass
                except Exception:
                    break
            # Return to the reservation page
            try:
                self.page.goto(self.url, wait_until="domcontentloaded", timeout=20000)
                self._human_idle(1.5, 3.0)
            except Exception:
                pass
        except Exception as e:
            print(f"[cart] clear_cart failed (non-fatal): {type(e).__name__}: {e}", flush=True)

    def _is_logged_in(self) -> bool:
        # Wait for JS to settle before checking — avoids false negatives from
        # cached DOM showing "Sign In" briefly before session cookies are applied.
        self.page.wait_for_timeout(2000)
        # A "Service Error" modal (e.g. form-token mismatch after submit) leaves
        # the user technically logged in but with a blocking overlay; treat it
        # as not-logged-in so the login retry path can clear cookies and recover.
        try:
            body_text = self.page.evaluate("document.body.innerText || ''")[:2000].lower()
            if "service error" in body_text or "form token" in body_text:
                return False
        except Exception:
            pass
        try:
            self.page.get_by_role("link", name=re.compile(r"sign in|log in", re.I)).first.wait_for(
                state="visible", timeout=3000
            )
            return False
        except Exception:
            return True

    def login(self) -> None:
        # Hard 4-minute budget for the whole login including retries.
        deadline = time.time() + 240
        last_error: Optional[Exception] = None
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                self._login_once()
                return
            except Exception as e:
                last_error = e
                msg = f"{type(e).__name__}: {e}"
                print(f"[login] attempt {attempt} failed: {msg}", flush=True)
                # Any failure → clear cookies before the next attempt. Stale
                # session state (form-token mismatch, Service Error modals,
                # half-applied cookies) is the dominant cause of "Sign In
                # didn't navigate" and other locator timeouts, and clearing
                # cookies is cheap, so do it unconditionally on every retry.
                self._clear_cookies_for_retry()
                self.page.wait_for_timeout(3000)
        raise RuntimeError(f"Login budget (4min) exhausted after {attempt} attempts. Last error: {last_error}")

    def _clear_cookies_for_retry(self) -> None:
        try:
            self.context.clear_cookies()
            print("[login] cookies cleared for retry (stale form-token recovery)", flush=True)
        except Exception as e:
            print(f"[login] cookie-clear failed (non-fatal): {e}", flush=True)

    def _login_once(self) -> None:
        for attempt in range(3):
            try:
                self.page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                break
            except Exception:
                if attempt == 2:
                    raise
                self.page.wait_for_timeout(3000)
        if self._is_logged_in():
            try:
                self.page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self.page.wait_for_timeout(int(random.uniform(1500, 3000)))
            print("Login complete (session reused).", flush=True)
            return
        # Not logged in. If the page is showing a Service Error / form-token
        # error modal, stale cookies are blocking — clear them and reload so
        # the login form is reachable. This is the proactive equivalent of
        # the retry-loop cookie clear, applied on first attempt too.
        try:
            body = self.page.evaluate("document.body.innerText || ''")[:2000].lower()
            if "service error" in body or "form token" in body:
                print("[login] Service Error / form-token on initial page — clearing cookies and reloading", flush=True)
                self._clear_cookies_for_retry()
                try:
                    self.page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                self.page.wait_for_timeout(1500)
        except Exception:
            pass
        self._dismiss_modal()
        # Wait for the loading bar to clear before clicking Sign In.
        try:
            self.page.locator(".loading-bar__outer-box").wait_for(state="hidden", timeout=15000)
        except Exception:
            pass
        sign_in = self.page.locator("a:has-text('Sign In'), a:has-text('Sign in now')").first
        sign_in.wait_for(state="visible", timeout=10000)
        # Prefer a real mouse click — el.click() via evaluate() is a synthetic
        # MouseEvent with no preceding pointer trajectory. Fall back to the JS
        # click only if the real click is intercepted by an overlay.
        try:
            self._human_mouse_to(sign_in)
            sign_in.click(timeout=5000)
        except Exception:
            sign_in.evaluate("el => el.click()")
        try:
            self.page.wait_for_url("**/signin**", timeout=30000)
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        self.page.wait_for_timeout(1000)
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
        # Beat between finishing typing and hitting submit — no human goes
        # last-keystroke → click in <100ms.
        self._human_idle(0.6, 1.4)
        # Save a pre-submit screenshot so we can see what was actually typed
        # if login fails.
        try:
            pre_shot = str(DATA_DIR / f"login_presubmit_{int(time.time())}.png")
            self.page.screenshot(path=pre_shot, full_page=True)
            print(f"[login] pre-submit shot={pre_shot}", flush=True)
        except Exception:
            pass
        try:
            submit_btn = self.page.get_by_role(
                "button", name=re.compile(r"sign in|log in", re.I)
            ).first
            self._human_mouse_to(submit_btn)
        except Exception:
            pass
        self._click_any(
            [
                ("role_button", r"sign in|log in"),
                ("css", "button[type='submit'], input[type='submit']"),
            ]
        )
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_timeout(int(random.uniform(2000, 4000)))
        try:
            self.page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        self.page.wait_for_timeout(int(random.uniform(1500, 3000)))
        # Verify login actually succeeded — silent failures here are why the
        # poll loop later sees "session expired" two seconds after "login complete".
        if not self._is_logged_in():
            try:
                shot = str(DATA_DIR / f"login_failed_{int(time.time())}.png")
                self.page.screenshot(path=shot, full_page=True)
                print(f"[login] failed — Sign In link still visible. shot={shot} url={self.page.url}", flush=True)
            except Exception:
                pass
            raise RuntimeError("Login submit didn't produce a logged-in page (credentials, captcha, or autofill)")
        print("Login complete.", flush=True)

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
                # Hover-then-click-then-type. fill() sets .value directly and
                # dispatches a single input event, which behavioral scoring can
                # distinguish from real keystrokes. Per-char type() fires
                # keydown/keypress/keyup at randomized intervals.
                self._human_mouse_to(locator)
                locator.click(timeout=10000)
                # Wait for Chrome autofill to fire (it triggers on focus).
                self.page.wait_for_timeout(random.randint(400, 700))
                # Multi-layer defeat: (a) JS-clear the value so any autofilled
                # text is gone; (b) Ctrl+A + Delete to clear any residual
                # selection / re-autofill; (c) immediately type with no wait
                # so autofill cannot fire again between clear and type.
                try:
                    locator.evaluate(
                        "el => { el.value = ''; el.dispatchEvent(new Event('input', {bubbles:true})); }"
                    )
                except Exception:
                    pass
                try:
                    locator.press("Control+a", timeout=2000)
                    locator.press("Delete", timeout=2000)
                except Exception:
                    pass
                for ch in value:
                    locator.type(ch, delay=random.randint(55, 165))
                return
            except Exception as exc:  # pragma: no cover - browser-dependent
                last_error = exc
        raise RuntimeError(f"Could not fill any expected selector: {selectors}") from last_error

    def open_target_day(self, target_date: datetime) -> None:
        # The date picker is a custom combobox (inputmode="none") — fill() is ignored.
        # Must click to open the calendar popup, navigate months, then click the target day.
        self._dismiss_modal()
        # The reservation grid renders a loading overlay (.loading-bar__outer-box)
        # over the page while fetching data. If we click the date picker while it
        # is still up, the click is intercepted and Playwright times out with the
        # locator resolved but actionability failing. Wait it out first, same as
        # login does.
        try:
            self.page.locator(".loading-bar__outer-box").wait_for(state="hidden", timeout=15000)
        except Exception:
            pass
        # Light pointer activity before touching the date picker — every page
        # visit feeds the reCAPTCHA score, not just the booking flow.
        self._human_idle(0.4, 1.0)
        date_input = self.page.get_by_label("Date picker, current date")
        date_input.wait_for(state="visible", timeout=15000)
        try:
            date_input.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        self._human_mouse_to(date_input)
        try:
            date_input.click(timeout=10000)
        except Exception:
            # Actionability check still failing — fall back to a JS click. This
            # sacrifices behavioral entropy for this one click, but the calendar
            # widget is far enough from the Reserve action that the trade is fine.
            date_input.evaluate("el => el.click()")
        self.page.locator(".an-calendar").wait_for(timeout=10000)
        self._human_idle(0.3, 0.7)

        target_ym = (target_date.year, target_date.month)
        reached_month = False
        for _ in range(24):
            header = self.page.locator(".an-calendar-header-title").inner_text(timeout=10000).strip()
            header_date = parse_calendar_header(header)
            if (header_date.year, header_date.month) == target_ym:
                reached_month = True
                break
            arrow = calendar_nav_arrow(header, target_date)
            arrow_loc = self.page.locator(arrow).first
            self._human_mouse_to(arrow_loc, settle_ms=(80, 220))
            arrow_loc.click()
            self.page.wait_for_timeout(random.randint(280, 620))

        # Fail loudly if month navigation never landed on the target month. The
        # old code fell through silently and then scraped/booked whatever month
        # happened to be showing — a layout change must surface as an error, not
        # a wrong-date booking.
        if not reached_month:
            raise RuntimeError(
                f"Calendar never reached {target_date.strftime('%b %Y')} after 24 steps"
            )

        day_str = str(target_date.day)
        day_cells = self.page.locator(".an-calendar-day:not(.an-calendar-day-othermonth)")
        count = day_cells.count()
        clicked_day = False
        for i in range(count):
            cell = day_cells.nth(i)
            if cell.inner_text(timeout=1000).strip() == day_str:
                self._human_mouse_to(cell)
                cell.click()
                clicked_day = True
                break

        # Same rationale: if no in-month cell matched the target day we have not
        # selected the intended date, so don't let scraping run against the
        # previously-selected day.
        if not clicked_day:
            raise RuntimeError(
                f"Day cell {day_str} not found in {target_date.strftime('%b %Y')}"
            )

        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self._human_idle(2.0, 3.4)

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

    def _human_mouse_jitter(self) -> None:
        """Small random cursor drift to seed pointer entropy."""
        try:
            x = random.randint(200, 1100)
            y = random.randint(150, 650)
            self.page.mouse.move(x, y, steps=random.randint(8, 18))
        except Exception:
            pass

    def _human_mouse_to(self, locator, settle_ms: Tuple[int, int] = (120, 350)) -> None:
        """Move cursor toward an element along a curved 2-hop path, then settle.

        reCAPTCHA Enterprise looks at pointer trajectories, not just final position.
        A straight `mouse.move(x, y, steps=N)` is linear in both axes; real users
        approach via an arc and overshoot/correct. Two hops with a jittered
        midpoint plus a randomized landing point inside the bbox approximate that
        cheaply.
        """
        try:
            box = locator.bounding_box()
            if not box:
                return
            tx = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            mx = tx + random.randint(-140, 140)
            my = ty + random.randint(-90, 90)
            self.page.mouse.move(mx, my, steps=random.randint(10, 18))
            self.page.mouse.move(tx, ty, steps=random.randint(12, 22))
            self.page.wait_for_timeout(random.randint(settle_ms[0], settle_ms[1]))
        except Exception:
            pass

    def _human_scroll(self) -> None:
        try:
            amt = random.randint(80, 260)
            if random.random() < 0.3:
                amt = -amt // 2
            self.page.mouse.wheel(0, amt)
        except Exception:
            pass

    def _human_idle(self, lo: float = 0.8, hi: float = 2.0) -> None:
        """Pause that also produces pointer activity, instead of a dead sleep.

        Drop-in replacement for `_human_pause` at points where the page is
        otherwise static (between clicks, after navigation). Reads as 'human
        glancing around' to behavioral scoring.
        """
        total_ms = int(random.uniform(lo, hi) * 1000)
        elapsed = 0
        while elapsed < total_ms:
            step = min(random.randint(180, 450), total_ms - elapsed)
            r = random.random()
            if r < 0.45:
                self._human_mouse_jitter()
            elif r < 0.6:
                self._human_scroll()
            self.page.wait_for_timeout(step)
            elapsed += step

    def _debug_capture(self, idx: int, step: str, force: bool = False, sensitive: bool = False) -> Optional[str]:
        """Save a screenshot + page HTML when BOOK_DEBUG=true, or on forced failure.

        `sensitive=True` marks the payment/checkout page: a full-page screenshot
        and HTML dump there would persist the card-on-file and the typed CVV (CVV
        fields are usually not masked) to disk. On those steps we log only the URL
        breadcrumb and skip the screenshot + HTML entirely.
        """
        if not force and os.getenv("BOOK_DEBUG", "false").lower() != "true":
            return
        if sensitive:
            try:
                print(f"[debug] {idx:02d} {step}: {self.page.url} (capture suppressed — payment page)", flush=True)
            except Exception:
                pass
            return None
        debug_dir = DATA_DIR / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-z0-9]+", "_", step.lower()).strip("_")
        base = debug_dir / f"{idx:02d}_{safe}"
        screenshot_path = str(base) + ".png"
        try:
            self.page.screenshot(path=screenshot_path, full_page=True)
        except Exception as e:
            print(f"[debug] screenshot failed at {step}: {e}", flush=True)
            screenshot_path = None
        try:
            html = self.page.content()
            (Path(str(base) + ".html")).write_text(html, encoding="utf-8")
        except Exception as e:
            print(f"[debug] html dump failed at {step}: {e}", flush=True)
        try:
            url = self.page.url
            print(f"[debug] {idx:02d} {step}: {url}", flush=True)
        except Exception:
            pass
        return screenshot_path

    def _page_text_lower(self) -> str:
        try:
            return self.page.locator("body").inner_text(timeout=1500).lower()
        except Exception:
            return ""

    def _raise_if_service_error(self) -> None:
        text = self._page_text_lower()
        service_error_markers = [
            "service error",
            "recaptcha verification failed",
            "please re-login",
        ]
        matched = next((m for m in service_error_markers if m in text), None)
        if matched:
            # Surface the matched marker + a window of text around it so we can
            # tell reCAPTCHA failure apart from generic service errors.
            idx = text.find(matched)
            start = max(0, idx - 120)
            end = min(len(text), idx + len(matched) + 200)
            window = re.sub(r"\s+", " ", text[start:end])
            raise RuntimeError(
                f"Service Error after Reserve [marker='{matched}']: ...{window}..."
            )

    def _find_cvv_input(self, timeout_ms: int = 45000):
        """Find a CVV/CVC/security-code field in any frame, regardless of iframe host."""
        deadline = time.time() + timeout_ms / 1000
        selectors = [
            'input[id*="cvv" i]',
            'input[name*="cvv" i]',
            'input[aria-label*="cvv" i]',
            'input[placeholder*="cvv" i]',
            'input[id*="cvc" i]',
            'input[name*="cvc" i]',
            'input[aria-label*="cvc" i]',
            'input[placeholder*="cvc" i]',
            'input[autocomplete="cc-csc"]',
            'input[id*="security" i]',
            'input[name*="security" i]',
        ]
        while time.time() < deadline:
            self._raise_if_service_error()
            for frame in self.page.frames:
                for selector in selectors:
                    try:
                        candidate = frame.locator(selector).first
                        if candidate.is_visible(timeout=250):
                            return candidate
                    except Exception:
                        continue
            self.page.wait_for_timeout(500)

        iframe_urls = []
        try:
            iframe_urls = [frame.url for frame in self.page.frames if frame.url and frame.url != self.page.url]
        except Exception:
            pass
        raise RuntimeError(f"CVV input not found in any frame. iframe_urls={iframe_urls}")

    def book_slot(self, slot: Slot) -> bool:
        if is_dry_run_enabled() and not is_preview_mode():
            return True
        step = "start"
        idx = 0
        # Once we navigate to the checkout/payment page, screenshots and HTML
        # dumps would capture the card-on-file and CVV. Track that so the failure
        # handler can suppress sending those to disk / Telegram.
        self.payment_page_reached = False
        # Wipe prior debug artifacts at the start of a debug run so we only keep
        # the captures from this attempt.
        if os.getenv("BOOK_DEBUG", "false").lower() == "true":
            debug_dir = DATA_DIR / "debug"
            if debug_dir.exists():
                for p in debug_dir.glob("*"):
                    try:
                        p.unlink()
                    except Exception:
                        pass
        try:
            self._debug_capture(idx, "before_click_cell"); idx += 1
            # Seed some pointer entropy *before* the first interaction. reCAPTCHA
            # Enterprise scores the whole session — a flow that goes straight from
            # page-load to clicking a grid cell with zero mouse activity in between
            # looks scripted regardless of what happens later.
            self._human_idle(0.8, 1.6)

            step = "click slot cell"
            print(f"[book_slot] {step}", flush=True)
            row = self.page.locator("tr").nth(slot.row_index)
            cell = row.locator("td.td-grid-cell, td.grid-cell").nth(slot.col_index)
            cell.scroll_into_view_if_needed(timeout=2500)
            self._human_idle(0.5, 1.2)
            self._human_mouse_to(cell)
            cell.click(timeout=2500)
            self._human_idle(1.5, 3.0)
            self._debug_capture(idx, "after_click_cell"); idx += 1

            # Fill in the required Event name field before confirming
            step = "fill event name"
            print(f"[book_slot] {step}", flush=True)
            event_name = os.getenv("BOOKING_EVENT_NAME", "Pickleball")
            try:
                name_input = self.page.get_by_label(re.compile("event name", re.I)).first
                self._human_mouse_to(name_input)
                name_input.click(timeout=10000)
                self._human_pause(0.3, 0.7)
                # Clear any prefilled value before typing. Without this, a prior
                # run that filled the field but never submitted leaves "Pickleball"
                # in place, and we end up posting "PickleballPickleball".
                try:
                    name_input.press("Control+a", timeout=2000)
                    name_input.press("Delete", timeout=2000)
                except Exception:
                    pass
                for ch in event_name:
                    name_input.type(ch, delay=random.randint(60, 160))
            except Exception as e:
                print(f"[book_slot] event name skipped: {e}", flush=True)

            self._human_idle(0.8, 1.8)
            self._debug_capture(idx, "after_event_name"); idx += 1

            step = "click Confirm Bookings"
            print(f"[book_slot] {step}", flush=True)
            try:
                confirm_btn = self.page.get_by_role(
                    "button", name=re.compile(r"confirm bookings?", re.I)
                ).first
                self._human_mouse_to(confirm_btn)
            except Exception:
                pass
            self._click_any(
                [
                    ("role_button", r"confirm bookings?"),
                    ("css", "button[class*='confirm']"),
                ]
            )
            self._human_idle(2.0, 4.0)
            self._debug_capture(idx, "after_confirm_bookings"); idx += 1

            step = "waiver checkbox + Save"
            print(f"[book_slot] {step}", flush=True)
            try:
                checkbox = self.page.locator("input[type='checkbox']").first
                checkbox.wait_for(timeout=4000)
                if not checkbox.is_checked():
                    self._human_idle(0.5, 1.0)
                    # Real mouse click instead of .check() — .check() sets the
                    # checked property programmatically and does not dispatch a
                    # full mousedown/mouseup pair. Behavioral scoring sees the
                    # difference.
                    self._human_mouse_to(checkbox)
                    try:
                        checkbox.click(timeout=2000)
                    except Exception:
                        checkbox.check()
                self._human_idle(0.5, 1.2)
                try:
                    save_btn = self.page.get_by_role(
                        "button", name=re.compile(r"save", re.I)
                    ).first
                    self._human_mouse_to(save_btn)
                except Exception:
                    pass
                self._click_any([("role_button", r"save"), ("css", "button[class*='save']")])
                self._human_idle(1.5, 3.0)
            except Exception as e:
                print(f"[book_slot] waiver/save skipped: {e}", flush=True)
            self._debug_capture(idx, "after_waiver_save"); idx += 1

            # Steady activity on the way to Reserve. Entropy is now spread across
            # the whole flow rather than crammed into the last second — that
            # 'sudden wiggle right before the gated action' pattern is itself a
            # signal.
            self._human_idle(1.4, 2.6)

            step = "click Reserve"
            print(f"[book_slot] {step}", flush=True)
            try:
                reserve_btn = self.page.get_by_role(
                    "button", name=re.compile(r"^reserve$", re.I)
                ).first
                self._human_mouse_to(reserve_btn)
            except Exception:
                pass
            self._click_any(
                [
                    ("role_button", r"^reserve$"),
                    ("css", "button.booking-detail__btn--continue"),
                ]
            )
            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            self._human_pause(2.0, 4.0)
            # Reserve navigates to the checkout/payment page from here on.
            self.payment_page_reached = True
            self._debug_capture(idx, "after_reserve", sensitive=True); idx += 1
            self._raise_if_service_error()

            # Preview mode: screenshot the payment page and stop
            if os.getenv("PREVIEW_STOP_BEFORE_PAY", "false").lower() == "true":
                screenshot_path = str(DATA_DIR / "preview_payment.png")
                self.page.screenshot(path=screenshot_path, full_page=True)
                send_telegram_photo(screenshot_path, "Reached payment page — not paying (preview mode)")
                print(f"[book_slot] preview mode stopped. Screenshot: {screenshot_path}", flush=True)
                return True

            # Checkout page — accept any waiver checkbox, fill CVV inside the payment iframe, then pay
            cvv = os.getenv("CREDIT_CARD_CVV", "")
            if cvv:
                try:
                    waiver = self.page.locator("input[type='checkbox']").first
                    if waiver.is_visible(timeout=2000) and not waiver.is_checked():
                        self._human_mouse_to(waiver)
                        try:
                            waiver.click(timeout=2000)
                        except Exception:
                            waiver.check()
                    self._human_idle(0.4, 0.9)
                except Exception:
                    pass
                self._debug_capture(idx, "after_checkout_waiver", sensitive=True); idx += 1

                # CVV + Pay are required to complete the booking — let any failure
                # bubble up so we don't report a successful booking that didn't happen.
                # The networkidle wait stays inside its own try/except because a slow
                # idle-check shouldn't void a payment that already went through.
                step = "fill CVV"
                print(f"[book_slot] {step}", flush=True)
                cvv_input = self._find_cvv_input(timeout_ms=45000)
                # Active Network's payment iframe is from a separate provider
                # that runs its own bot/fraud signals. fill() sets .value and
                # dispatches a single input event; per-char type() fires real
                # keydown/keyup like a human entering a CVV.
                try:
                    cvv_input.click(timeout=5000)
                    self._human_pause(0.3, 0.7)
                    cvv_input.press("Control+a", timeout=2000)
                    cvv_input.press("Delete", timeout=2000)
                except Exception:
                    pass
                for ch in cvv:
                    cvv_input.type(ch, delay=random.randint(80, 200))
                self._human_idle(0.4, 0.9)
                self._debug_capture(idx, "after_cvv", sensitive=True); idx += 1
                step = "click Pay"
                print(f"[book_slot] {step}", flush=True)
                self._click_any([("role_button", r"^pay$"), ("css", "button[class*='pay']")])
                try:
                    self.page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                self.page.wait_for_timeout(2000)
                self._debug_capture(idx, "after_pay", sensitive=True); idx += 1

            print("[book_slot] done", flush=True)
            return True
        except Exception as e:
            print(f"[book_slot] FAILED at step '{step}': {type(e).__name__}: {e}", flush=True)
            if getattr(self, "payment_page_reached", False):
                # On the checkout/payment page — a screenshot would capture card
                # data. Send a text-only alert instead.
                send_telegram(
                    f"Booking failed at '{step}' on the payment page: "
                    f"{type(e).__name__} (screenshot suppressed to avoid capturing card data)"
                )
            else:
                shot = self._debug_capture(99, f"FAILED_{step}", force=True)
                if shot:
                    send_telegram_photo(shot, f"Booking failed at {step}: {type(e).__name__}")
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


def next_booking_weekend(reference: datetime) -> Tuple[datetime, datetime]:
    """The weekend that the upcoming Sunday cron run will target."""
    days_until_sunday = (6 - reference.weekday()) % 7
    next_sunday = reference + timedelta(days=days_until_sunday)
    return upcoming_weekend(next_sunday)


def get_weekend_decision(saturday: datetime, sunday: datetime) -> Optional[str]:
    """Returns 'confirmed', 'declined', or None."""
    payload = read_json_file(WEEKEND_DECISIONS_FILE, {})
    return payload.get(weekend_key(saturday, sunday))


def set_weekend_decision(saturday: datetime, sunday: datetime, decision: str) -> None:
    payload = read_json_file(WEEKEND_DECISIONS_FILE, {})
    payload[weekend_key(saturday, sunday)] = decision
    write_json_file(WEEKEND_DECISIONS_FILE, payload)


def save_pending_weekend(saturday: datetime, sunday: datetime) -> None:
    write_json_file(PENDING_WEEKEND_FILE, {
        "saturday": saturday.isoformat(),
        "sunday": sunday.isoformat(),
    })


def load_pending_weekend() -> Optional[dict]:
    return read_json_file(PENDING_WEEKEND_FILE, None)


def clear_pending_weekend() -> None:
    if PENDING_WEEKEND_FILE.exists():
        PENDING_WEEKEND_FILE.unlink()


def preferred_hour_priority() -> List[int]:
    """Start hours to prefer within the preferred window, best first.

    Defaults to 10am → 9am → 8am. Hours in the preferred window but absent from
    this list (e.g. 11am) rank after every listed hour, so they're only booked
    when nothing better is open."""
    raw = os.getenv("PREFERRED_HOUR_PRIORITY", "10,9,8")
    return [int(p) for p in raw.split(",") if p.strip().lstrip("-").isdigit()]


def choose_auto_book_slot(saturday_slots: List[Slot], sunday_slots: List[Slot]) -> Optional[Slot]:
    priority = preferred_hour_priority()
    preferred = [s for s in (saturday_slots + sunday_slots) if is_preferred_time(s)]
    if not preferred:
        return None

    def rank(slot: Slot) -> tuple:
        try:
            hour_rank = priority.index(slot.start.hour)
        except ValueError:
            hour_rank = len(priority)
        # Secondary key keeps Saturday before Sunday (earlier absolute start)
        # and gives a stable court order within the same hour.
        return (hour_rank, slot.start, slot.resource_name)

    return min(preferred, key=rank)


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


def prune_old_screenshots(max_age_days: int = 7) -> None:
    """Delete diagnostic PNGs in data/ older than `max_age_days`.

    login_presubmit_*, login_failed_*, logout_*, captcha_*, last_run_* and
    preview_payment screenshots are written on most runs and were never cleaned
    up, so they accumulate indefinitely on a long-lived host. Keep a rolling
    week for debugging and drop the rest. Best-effort — never fatal.
    """
    cutoff = time.time() - max_age_days * 86400
    patterns = ("login_presubmit_*.png", "login_failed_*.png", "logout_*.png",
                "captcha_*.png", "last_run_*.png")
    for pattern in patterns:
        for p in DATA_DIR.glob(pattern):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass


def main() -> None:
    run_id = uuid.uuid4().hex[:8].upper()

    chicago = ZoneInfo("America/Chicago")
    now_ct = datetime.now(chicago)
    prune_old_screenshots()

    def log(msg: str) -> None:
        ts = datetime.now(chicago).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [RUN {run_id}] {msg}", flush=True)

    # Hard 25-minute kill switch. Booking attempts should never run longer than
    # this — if they do, something is genuinely hung (the May 20 incident was a
    # 58-minute stuck-login that blew past the cron deadline). SIGALRM only
    # works on the main thread of POSIX systems, which is fine for our cron.
    import signal as _signal
    def _hard_timeout(_sig, _frame):
        try:
            send_telegram(f"⏰ Pickleball booker [{run_id}] hit 25-min process timeout — bailing.")
        except Exception:
            pass
        print(f"[ALARM] [RUN {run_id}] hard 25-min process timeout — exiting", flush=True)
        os._exit(2)
    try:
        _signal.signal(_signal.SIGALRM, _hard_timeout)
        _signal.alarm(25 * 60)
    except (AttributeError, ValueError):
        pass  # Windows / non-main-thread — SIGALRM unavailable

    if is_dry_run_enabled():
        mode = "dry-run"
    elif is_preview_mode():
        mode = "preview"
    elif is_cron_mode():
        mode = "cron"
    else:
        mode = "manual"

    saturday, sunday = upcoming_weekend(now_ct)
    log(f"start mode={mode} target={saturday.date()}/{sunday.date()}")

    # Cron-only weekday gating. Manual/dry/preview runs always proceed.
    #
    # The week is split into two phases for any given weekend X:
    #   • Fri / Sat (8 / 7 days before X) — ASK YES/NO. No booking attempt.
    #     `next_booking_weekend` returns X here (the weekend whose booking
    #     window opens on the upcoming Sunday).
    #   • Sun – Thu (6–2 days before X) — ATTEMPT to book X.
    #     `upcoming_weekend` returns X here. Decline check applies.
    # Once declined (Fri/Sat NO or any "Don't book this weekend" tap), all
    # further activity for that weekend is silent.
    if is_cron_mode():
        weekday = now_ct.weekday()  # Mon=0 ... Sun=6

        if weekday in (4, 5):  # Fri or Sat → ASK about next_booking_weekend
            target_sat, target_sun = next_booking_weekend(now_ct)
            existing = get_weekend_decision(target_sat, target_sun)
            if existing == "declined":
                log(f"end status=skipped reason=user_declined_weekend {target_sat.date()}")
                return
            if existing == "confirmed":
                log(f"end status=already_confirmed weekend={target_sat.date()}")
                return
            send_telegram(
                f"📅 Pickleball booking window opens Sunday for the weekend of "
                f"{target_sat.strftime('%a %b %-d')} / "
                f"{target_sun.strftime('%a %b %-d')}.\n\n"
                f"If you don't reply, I'll start trying to book by default.",
                inline_keyboard=[[("✅ YES, book it", "WEEK_YES"), ("❌ NO, skip", "WEEK_NO")]],
            )
            save_pending_weekend(target_sat, target_sun)
            log(f"end status=awaiting_confirmation weekend={target_sat.date()}")
            return

        # Sun – Thu → ATTEMPT booking for upcoming_weekend (6–2 days away).
        decision = get_weekend_decision(saturday, sunday)
        if decision == "declined":
            log(f"end status=skipped reason=user_declined_weekend {saturday.date()}")
            clear_pending_weekend()
            return
        clear_pending_weekend()

    # Cron now runs daily, so always send the fallback slot list when no preferred
    # slot is found. Email notifications still only fire on Sun/Mon to avoid
    # spamming an email inbox seven times a week (Telegram is the daily channel).
    send_email_no_avail = is_dry_run_enabled() or now_ct.weekday() in (6, 0)
    if is_test_run():
        # Test runs (dry-run or preview) use a rolling window so they work any time of day.
        deadline_ct = dry_run_poll_deadline_ct(now_ct)
    elif is_cron_mode():
        deadline_ct = now_ct.replace(hour=7, minute=30, second=0, microsecond=0)
    else:
        # On-demand manual run: 10-minute window starting now.
        deadline_ct = now_ct + timedelta(minutes=10)

    # Short-circuit silently if the weekend was already booked on an earlier
    # cron run. The user already got a "Booked" confirmation + calendar invite
    # for this weekend — they don't need a heartbeat or "skipped" message
    # every subsequent run.
    if has_weekend_booking_lock(saturday, sunday):
        log("end status=skipped reason=weekend_already_booked")
        return

    # Daily cron no longer sends a heartbeat — the scrape always ends in either
    # a booking confirmation, a slot-list message, or a "no slots at all"
    # message, so the user already gets a signal whenever the cron actually did
    # work. The heartbeat would just add a 7×/week noise message.

    status = "unknown"
    reason = ""
    booked = False
    last_all_slots: List[Slot] = []

    try:
        with CPDBooker() as booker:
            log("opening browser + logging in")
            booker.login()
            log("login complete")
            # Verify the fingerprint patches actually applied. If reCAPTCHA keeps
            # failing despite this output looking right, the score is being
            # determined by something beyond what we patch (behavioral, IP, or
            # persisted cookies on the profile).
            booker.log_fingerprint_diagnostics()
            # Drop any leftover items in the cart from a prior failed run. A
            # stale cart inflates the total AND looks bot-shaped at Reserve.
            booker.clear_cart()

            if booker.captcha_visible():
                log("CAPTCHA detected after login")
                captcha_shot = str(DATA_DIR / f"captcha_{run_id}.png")
                try:
                    booker.page.screenshot(path=captcha_shot, full_page=True)
                except Exception as e:
                    log(f"captcha screenshot failed: {e}")
                send_telegram(
                    f"⚠️ Captcha blocking pickleball booker [{run_id}] — "
                    f"log into the VPS browser profile and solve it manually."
                )
                send_telegram_photo(captcha_shot, f"Captcha on run [{run_id}]")
                status = "captcha"
                reason = "captcha_after_login"
                return

            # Reset deadline after login so the polling window starts when scraping can actually begin.
            if is_test_run():
                deadline_ct = dry_run_poll_deadline_ct(datetime.now(chicago))
            elif not is_cron_mode():
                deadline_ct = datetime.now(chicago) + timedelta(minutes=10)

            max_polls = 1 if is_test_run() else None
            poll_count = 0
            while datetime.now(chicago) < deadline_ct:
                if max_polls and poll_count >= max_polls:
                    break
                poll_count += 1
                if not booker._is_logged_in():
                    try:
                        shot = str(DATA_DIR / f"logout_{run_id}_p{poll_count}.png")
                        booker.page.screenshot(path=shot, full_page=True)
                        log(f"session expired at poll #{poll_count} url={booker.page.url} shot={shot} — re-logging in")
                    except Exception as _e:
                        log(f"session expired at poll #{poll_count} — re-logging in (screenshot failed: {_e})")
                    booker.login()
                booker.open_target_day(saturday)
                saturday_slots = booker.scrape_slots(saturday, "Saturday")
                booker.open_target_day(sunday)
                sunday_slots = booker.scrape_slots(sunday, "Sunday")
                last_all_slots = sorted(saturday_slots + sunday_slots, key=lambda s: s.start)
                pref_count = sum(1 for s in last_all_slots if is_preferred_time(s))
                log(
                    f"poll #{poll_count}: sat={len(saturday_slots)} sun={len(sunday_slots)} "
                    f"total={len(last_all_slots)} preferred={pref_count}"
                )

                picked_auto = choose_auto_book_slot(saturday_slots, sunday_slots)
                if picked_auto:
                    log(f"attempting auto-book: {format_slot(picked_auto)}")
                    target_date = saturday if picked_auto.day_label == "Saturday" else sunday
                    booker.open_target_day(target_date)
                    if booker.captcha_visible():
                        log("CAPTCHA appeared before booking")
                        send_telegram(f"⚠️ Captcha before booking [{run_id}]")
                        status = "captcha"
                        reason = "captcha_before_booking"
                        break
                    success = booker.book_slot(picked_auto)
                    if success and not is_dry_run_enabled():
                        set_weekend_booking_lock(saturday, sunday, picked_auto, run_id)
                        send_calendar_invite(picked_auto)
                    outcome = "Would book (dry run)" if is_dry_run_enabled() else ("Booked" if success else "Failed booking")
                    send_telegram(f"{outcome}: {format_slot(picked_auto)}")
                    booked = success
                    status = "booked" if success else "book_failed"
                    reason = format_slot(picked_auto)
                    break

                # On-demand manual runs: skip polling for cancellations and send the
                # fallback options right away so the user can pick a slot now.
                if not is_cron_mode() and not is_test_run() and last_all_slots:
                    log("manual run: stopping poll loop — sending fallback options")
                    break

                # 5s polls trigger CPD's bot-detection. 20s is well within the
                # 10-minute cron window (~30 polls) while looking less mechanical.
                time.sleep(20)

            # Save a final state snapshot so we always have something to look at.
            try:
                last_shot = str(DATA_DIR / f"last_run_{run_id}.png")
                booker.page.screenshot(path=last_shot, full_page=True)
                log(f"final screenshot saved: {last_shot}")
            except Exception as e:
                log(f"final screenshot failed: {e}")

    except Exception as e:
        log(f"CRASH: {type(e).__name__}: {e}")
        status = "crashed"
        reason = f"{type(e).__name__}: {e}"
        send_telegram(f"\U0001F4A5 Pickleball booker [{run_id}] crashed: {type(e).__name__}: {str(e)[:200]}")

    if not booked and status == "unknown":
        status = "no_preferred_slot" if last_all_slots else "no_slots"

    if not booked and status not in ("captcha", "crashed"):
        # Email only on Sun/Mon cron runs (or dry-run testing), never in test mode.
        if send_email_no_avail and not is_test_run():
            send_no_availability_email(saturday, sunday)
        # Telegram options: always send when we have a result to surface.
        if not last_all_slots:
            send_telegram(
                f"No pickleball slots found at all for "
                f"{saturday.strftime('%b %-d')}–{sunday.strftime('%b %-d')}."
            )
        else:
            time_groups = group_slots_by_time(filter_display_slots(last_all_slots))
            save_pending_choice(run_id, saturday, sunday, time_groups)
            send_slot_options(
                time_groups,
                f"No preferred {preferred_hours_display()} slot found. Tap a time to book — I'll pick an available court:",
                run_id=run_id,
            )

    log(f"end status={status} reason={reason} polls={'n/a' if status == 'crashed' else ''}")


if __name__ == "__main__":
    main()
