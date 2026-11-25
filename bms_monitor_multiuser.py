import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright
from plyer import notification


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CDP_URL = os.environ.get(
    "BMS_CDP_URL",
    "http://127.0.0.1:9222",
)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Optional comma-separated allowlist.
#
# Example:
# ALLOWED_CHAT_IDS=123456789,987654321
#
# When unset, anyone who finds the bot can use it.
ALLOWED_CHAT_IDS_RAW = os.environ.get("ALLOWED_CHAT_IDS", "")

CHECK_MINUTES = [1, 11, 21, 31, 41, 51]
TELEGRAM_POLL_SECONDS = 2

MAX_MONITORS_PER_USER = 3
MAX_MONITORS_TOTAL = 10

# BookMyShow URL configuration
BMS_CITY = os.environ.get("BMS_CITY", "coimbatore")
BMS_MOVIE_SLUG = os.environ.get("BMS_MOVIE_SLUG", "ramayana")
BMS_LANGUAGE = os.environ.get("BMS_LANGUAGE", "tamil")


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# Structure:
#
# monitors = {
#     "chat_id": [
#         {
#             "event_code": "ET00523959",
#             "target_date": "20261128",
#             "url": "...",
#             "next_check": datetime(...),
#             "created_at": datetime(...),
#             "last_result": "Waiting for first check",
#             "last_checked_at": None,
#         }
#     ]
# }
monitors = {}

telegram_offset = None


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def parse_allowed_chat_ids():
    if not ALLOWED_CHAT_IDS_RAW.strip():
        return set()

    return {
        item.strip()
        for item in ALLOWED_CHAT_IDS_RAW.split(",")
        if item.strip()
    }


ALLOWED_CHAT_IDS = parse_allowed_chat_ids()


def is_chat_allowed(chat_id):
    if not ALLOWED_CHAT_IDS:
        return True

    return str(chat_id) in ALLOWED_CHAT_IDS


def build_url(event_code, target_date):
    return (
        f"https://in.bookmyshow.com/movies/"
        f"{BMS_CITY}/{BMS_MOVIE_SLUG}/buytickets/"
        f"{event_code}/{target_date}"
        f"?etCodes={event_code}"
        f"&language={BMS_LANGUAGE}"
        f"&refEventCode={event_code}"
    )


def validate_event_code(event_code):
    return (
        event_code.startswith("ET")
        and len(event_code) > 2
        and event_code[2:].isdigit()
    )


def validate_target_date(target_date):
    if len(target_date) != 8 or not target_date.isdigit():
        return False

    try:
        datetime.strptime(target_date, "%Y%m%d")
        return True
    except ValueError:
        return False


def format_date(target_date):
    try:
        return datetime.strptime(
            target_date,
            "%Y%m%d",
        ).strftime("%d %b %Y")
    except ValueError:
        return target_date


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def calculate_next_check():
    """
    Return the next :01, :11, :21, :31, :41 or :51 boundary.
    """

    now = datetime.now()

    for minute in CHECK_MINUTES:
        candidate = now.replace(
            minute=minute,
            second=0,
            microsecond=0,
        )

        if candidate > now:
            return candidate

    next_hour = now + timedelta(hours=1)

    return next_hour.replace(
        minute=1,
        second=0,
        microsecond=0,
    )


# ---------------------------------------------------------------------------
# PowerShell integration
# ---------------------------------------------------------------------------

def run_powershell(script, extra_env=None, timeout=40):
    env = os.environ.copy()

    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()

        raise RuntimeError(
            error or "PowerShell command failed."
        )

    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(chat_id, message):
    script = r"""
$body = @{
    chat_id = $env:BMS_CHAT_ID
    text = $env:BMS_MESSAGE
    disable_web_page_preview = $true
}

Invoke-RestMethod `
    -Uri "https://api.telegram.org/bot$env:TELEGRAM_BOT_TOKEN/sendMessage" `
    -Method Post `
    -Body $body | Out-Null
"""

    try:
        run_powershell(
            script,
            {
                "BMS_CHAT_ID": str(chat_id),
                "BMS_MESSAGE": message,
            },
        )

        return True

    except Exception as error:
        print(
            f"Telegram send failed for chat {chat_id}:",
            error,
        )
        return False


def get_telegram_updates(offset=None):
    offset_value = "" if offset is None else str(offset)

    script = r"""
$params = @{
    timeout = 0
}

if ($env:BMS_UPDATE_OFFSET) {
    $params["offset"] = $env:BMS_UPDATE_OFFSET
}

$response = Invoke-RestMethod `
    -Uri "https://api.telegram.org/bot$env:TELEGRAM_BOT_TOKEN/getUpdates" `
    -Method Get `
    -Body $params

$response | ConvertTo-Json -Depth 20 -Compress
"""

    try:
        output = run_powershell(
            script,
            {
                "BMS_UPDATE_OFFSET": offset_value,
            },
        )

        if not output:
            return []

        response = json.loads(output)

        if not response.get("ok"):
            return []

        return response.get("result", [])

    except Exception as error:
        print("Telegram polling failed:", error)
        return []


def flush_old_telegram_updates():
    global telegram_offset

    updates = get_telegram_updates()

    if updates:
        telegram_offset = max(
            update["update_id"]
            for update in updates
        ) + 1


# ---------------------------------------------------------------------------
# Monitor state
# ---------------------------------------------------------------------------

def total_monitor_count():
    return sum(
        len(user_monitors)
        for user_monitors in monitors.values()
    )


def find_monitor(chat_id, event_code, target_date):
    user_monitors = monitors.get(str(chat_id), [])

    for monitor in user_monitors:
        if (
            monitor["event_code"] == event_code
            and monitor["target_date"] == target_date
        ):
            return monitor

    return None


def add_monitor(chat_id, event_code, target_date):
    chat_id = str(chat_id)

    if not validate_event_code(event_code):
        return (
            "Invalid event code.\n\n"
            "Example:\n"
            "/monitor ET00523959 20261128"
        )

    if not validate_target_date(target_date):
        return (
            "Invalid date. Use YYYYMMDD.\n\n"
            "Example:\n"
            "/monitor ET00523959 20261128"
        )

    if find_monitor(chat_id, event_code, target_date):
        return (
            "That monitor is already active.\n\n"
            f"Event: {event_code}\n"
            f"Date: {format_date(target_date)}"
        )

    user_monitors = monitors.setdefault(chat_id, [])

    if len(user_monitors) >= MAX_MONITORS_PER_USER:
        return (
            "You have reached the monitor limit.\n\n"
            f"Maximum per user: {MAX_MONITORS_PER_USER}\n\n"
            "Use /list to view your monitors."
        )

    if total_monitor_count() >= MAX_MONITORS_TOTAL:
        return (
            "The bot has reached its total monitor limit.\n\n"
            f"Maximum active monitors: {MAX_MONITORS_TOTAL}"
        )

    monitor = {
        "event_code": event_code,
        "target_date": target_date,
        "url": build_url(event_code, target_date),
        "next_check": datetime.now(),
        "created_at": datetime.now(),
        "last_checked_at": None,
        "last_result": "Waiting for first check",
    }

    user_monitors.append(monitor)

    return (
        "BookMyShow monitor started.\n\n"
        f"Event: {event_code}\n"
        f"Date: {format_date(target_date)}\n\n"
        "The first check will run shortly.\n"
        "Later checks run at:\n"
        ":01, :11, :21, :31, :41 and :51."
    )


def remove_monitor(chat_id, event_code, target_date):
    chat_id = str(chat_id)
    user_monitors = monitors.get(chat_id, [])

    monitor = find_monitor(
        chat_id,
        event_code,
        target_date,
    )

    if monitor is None:
        return (
            "Monitor not found.\n\n"
            "Use /list to view your active monitors."
        )

    user_monitors.remove(monitor)

    if not user_monitors:
        monitors.pop(chat_id, None)

    return (
        "Monitor stopped.\n\n"
        f"Event: {event_code}\n"
        f"Date: {format_date(target_date)}"
    )


def remove_all_user_monitors(chat_id):
    chat_id = str(chat_id)
    count = len(monitors.get(chat_id, []))

    if count == 0:
        return "You have no active monitors."

    monitors.pop(chat_id, None)

    return f"Stopped {count} active monitor(s)."


def list_user_monitors(chat_id):
    user_monitors = monitors.get(str(chat_id), [])

    if not user_monitors:
        return "You have no active BookMyShow monitors."

    lines = ["YOUR ACTIVE MONITORS", ""]

    for index, monitor in enumerate(
        user_monitors,
        start=1,
    ):
        next_check = monitor["next_check"]

        if next_check:
            next_check_text = next_check.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            next_check_text = "Pending"

        lines.extend(
            [
                f"{index}. {monitor['event_code']}",
                f"Date: {format_date(monitor['target_date'])}",
                f"Next check: {next_check_text}",
                f"Last result: {monitor['last_result']}",
                "",
            ]
        )

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def send_windows_notification(
    event_code,
    target_date,
):
    try:
        notification.notify(
            title="BookMyShow Booking Open!",
            message=(
                f"{event_code} is available "
                f"for {format_date(target_date)}."
            ),
            app_name="BookMyShow Monitor",
            timeout=30,
        )
    except Exception as error:
        print("Windows notification failed:", error)


# ---------------------------------------------------------------------------
# BookMyShow checking
# ---------------------------------------------------------------------------

def check_booking(page, monitor):
    event_code = monitor["event_code"]
    target_date = monitor["target_date"]
    url = monitor["url"]

    now = datetime.now()

    print()
    print(
        f"[{now:%Y-%m-%d %H:%M:%S}] "
        f"Checking {event_code} / {target_date}"
    )

    monitor["last_checked_at"] = now

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    # Allow BookMyShow to redirect and load showtimes.
    page.wait_for_timeout(8000)

    final_url = page.url
    body_text = page.locator("body").inner_text()

    security_text = (
        "security service to protect itself from online attacks"
    )

    if security_text.lower() in body_text.lower():
        monitor["last_result"] = "Security page detected"
        print("BookMyShow security page detected.")
        return False

    target_in_url = f"/{target_date}" in final_url

    # This signal was validated against the working booking page.
    has_showtimes = "Non-cancellable" in body_text

    if target_in_url and has_showtimes:
        monitor["last_result"] = "Booking open"
        print("BOOKING OPEN")
        return True

    if not target_in_url:
        monitor["last_result"] = (
            "Not open: redirected away"
        )
        print(
            "NOT OPEN: BookMyShow redirected away "
            "from the requested date."
        )
    else:
        monitor["last_result"] = (
            "Target date loaded, no showtimes"
        )
        print(
            "Target date loaded, but no showtimes "
            "were detected."
        )

    return False


def handle_booking_open(chat_id, monitor):
    event_code = monitor["event_code"]
    target_date = monitor["target_date"]
    url = monitor["url"]

    message = (
        "BOOKMYSHOW BOOKING OPEN!\n\n"
        f"Event: {event_code}\n"
        f"Date: {format_date(target_date)}\n\n"
        f"Book now:\n{url}"
    )

    print()
    print("=" * 65)
    print("BOOKING IS OPEN")
    print("Chat :", chat_id)
    print("Event:", event_code)
    print("Date :", target_date)
    print("=" * 65)

    send_windows_notification(
        event_code,
        target_date,
    )

    send_telegram(chat_id, message)

    user_monitors = monitors.get(str(chat_id), [])

    if monitor in user_monitors:
        user_monitors.remove(monitor)

    if not user_monitors:
        monitors.pop(str(chat_id), None)


def process_due_monitors(page):
    now = datetime.now()

    # Build a snapshot because successful monitors are
    # removed while this loop is running.
    due_monitors = []

    for chat_id, user_monitors in monitors.items():
        for monitor in user_monitors:
            if monitor["next_check"] <= now:
                due_monitors.append(
                    (chat_id, monitor)
                )

    for chat_id, monitor in due_monitors:
        # It may have been removed by /stop while queued.
        if monitor not in monitors.get(chat_id, []):
            continue

        try:
            booking_open = check_booking(
                page,
                monitor,
            )

            if booking_open:
                handle_booking_open(
                    chat_id,
                    monitor,
                )
            else:
                monitor["next_check"] = (
                    calculate_next_check()
                )

                print(
                    "Next check:",
                    monitor["next_check"].strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                )

        except Exception as error:
            monitor["last_result"] = (
                f"Check failed: {error}"
            )
            monitor["next_check"] = (
                calculate_next_check()
            )

            print(
                f"Check failed for {chat_id} / "
                f"{monitor['event_code']}:",
                error,
            )


# ---------------------------------------------------------------------------
# Telegram command processing
# ---------------------------------------------------------------------------

def help_message():
    return (
        "BOOKMYSHOW MONITOR COMMANDS\n\n"
        "/monitor EVENT_CODE YYYYMMDD\n"
        "Add a new monitor.\n\n"
        "Example:\n"
        "/monitor ET00523959 20261128\n\n"
        "/list\n"
        "Show your active monitors.\n\n"
        "/stop EVENT_CODE YYYYMMDD\n"
        "Stop one monitor.\n\n"
        "/stopall\n"
        "Stop all your monitors.\n\n"
        "/help\n"
        "Show this help message.\n\n"
        f"Per-user limit: {MAX_MONITORS_PER_USER}\n"
        f"Total bot limit: {MAX_MONITORS_TOTAL}"
    )


def process_telegram_message(message):
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return

    if not is_chat_allowed(chat_id):
        send_telegram(
            chat_id,
            "This bot is private. Access denied.",
        )
        return

    parts = text.split()
    command = parts[0].split("@")[0].lower()

    if command == "/monitor":
        if len(parts) != 3:
            response = (
                "Usage:\n"
                "/monitor EVENT_CODE YYYYMMDD\n\n"
                "Example:\n"
                "/monitor ET00523959 20261128"
            )
        else:
            response = add_monitor(
                chat_id,
                parts[1].upper(),
                parts[2],
            )

    elif command in ("/list", "/status"):
        response = list_user_monitors(chat_id)

    elif command == "/stop":
        if len(parts) != 3:
            response = (
                "Usage:\n"
                "/stop EVENT_CODE YYYYMMDD"
            )
        else:
            response = remove_monitor(
                chat_id,
                parts[1].upper(),
                parts[2],
            )

    elif command == "/stopall":
        response = remove_all_user_monitors(
            chat_id
        )

    elif command in ("/start", "/help"):
        response = help_message()

    else:
        response = (
            "Unknown command.\n\n"
            "Send /help for available commands."
        )

    send_telegram(chat_id, response)


def poll_telegram():
    global telegram_offset

    updates = get_telegram_updates(
        telegram_offset
    )

    for update in updates:
        telegram_offset = (
            update["update_id"] + 1
        )

        message = update.get("message")

        if message:
            process_telegram_message(message)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def print_startup():
    print()
    print("BookMyShow Multi-User Telegram Monitor")
    print("=" * 55)
    print("Chrome endpoint :", CDP_URL)
    print("Maximum total   :", MAX_MONITORS_TOTAL)
    print("Maximum per user:", MAX_MONITORS_PER_USER)
    print("Check schedule  : :01 :11 :21 :31 :41 :51")
    print(
        "Access mode     :",
        (
            f"Restricted to {len(ALLOWED_CHAT_IDS)} chat(s)"
            if ALLOWED_CHAT_IDS
            else "PUBLIC - all Telegram users"
        ),
    )
    print()
    print("Press Ctrl+C to stop.")
    print()


if not TELEGRAM_BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN is not set.")
    sys.exit(2)


print_startup()


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            CDP_URL
        )

        if not browser.contexts:
            print("ERROR: No Chrome context found.")
            sys.exit(2)

        context = browser.contexts[0]

        if context.pages:
            page = context.pages[0]
        else:
            page = context.new_page()

        print("Connected to Chrome.")

        flush_old_telegram_updates()

        print("Telegram bot is online.")
        print()

        while True:
            try:
                poll_telegram()
                process_due_monitors(page)

            except Exception as error:
                print("Processing error:", error)

            time.sleep(TELEGRAM_POLL_SECONDS)


except KeyboardInterrupt:
    print()
    print("BookMyShow monitor stopped.")


except Exception as error:
    print()
    print("ERROR:", error)
    print(
        "Make sure Chrome is running with "
        "--remote-debugging-port=9222."
    )
