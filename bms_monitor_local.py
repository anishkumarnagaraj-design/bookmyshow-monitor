import os
import sys
import time
import subprocess
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright
from plyer import notification


CDP_URL = "http://127.0.0.1:9222"
CHECK_MINUTES = [1, 11, 21, 31, 41, 51]


if len(sys.argv) != 3:
    print("Usage: py bms_monitor.py EVENT_CODE YYYYMMDD")
    print("Example: py bms_monitor.py ET00523959 20261128")
    sys.exit(2)


EVENT_CODE = sys.argv[1]
TARGET_DATE = sys.argv[2]

URL = (
    "https://in.bookmyshow.com/movies/coimbatore/ramayana/buytickets/"
    f"{EVENT_CODE}/{TARGET_DATE}"
    f"?etCodes={EVENT_CODE}"
    "&language=tamil"
    f"&refEventCode={EVENT_CODE}"
)


def next_check_time():
    """
    Return the next wall-clock time whose minute is:
    01, 11, 21, 31, 41, or 51.
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

    # We have passed :51, so next check is :01 next hour.
    next_hour = now + timedelta(hours=1)

    return next_hour.replace(
        minute=1,
        second=0,
        microsecond=0,
    )


def wait_for_next_check():
    target = next_check_time()
    wait_seconds = (target - datetime.now()).total_seconds()

    print(
        "Next check:",
        target.strftime("%Y-%m-%d %H:%M:%S"),
    )
    print()

    if wait_seconds > 0:
        time.sleep(wait_seconds)


def send_windows_notification():
    try:
        notification.notify(
            title="BookMyShow Booking Open!",
            message=(
                f"Booking detected for {TARGET_DATE}. "
                "Check BookMyShow now!"
            ),
            app_name="BookMyShow Monitor",
            timeout=30,
        )

        print("Windows notification sent.")

    except Exception as e:
        print("Windows notification failed:", e)


def send_telegram_notification():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram variables are not configured.")
        return

    message = (
        "BOOKMYSHOW BOOKING OPEN!\n\n"
        f"Event: {EVENT_CODE}\n"
        f"Date: {TARGET_DATE}\n\n"
        f"Book now:\n{URL}"
    )

    # Use PowerShell because it already works correctly
    # with your company's TLS/certificate setup.
    ps_script = """
$body = @{
    chat_id = $env:TELEGRAM_CHAT_ID
    text = $env:BMS_TELEGRAM_MESSAGE
}

Invoke-RestMethod `
    -Uri "https://api.telegram.org/bot$env:TELEGRAM_BOT_TOKEN/sendMessage" `
    -Method Post `
    -Body $body | Out-Null
"""

    env = os.environ.copy()
    env["BMS_TELEGRAM_MESSAGE"] = message

    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ps_script,
            ],
            env=env,
            check=True,
        )

        print("Telegram notification sent.")

    except Exception as e:
        print("Telegram notification failed:", e)


def check_booking(page):
    now = datetime.now()

    print(
        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] "
        "Checking BookMyShow..."
    )

    page.goto(
        URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    # Give BookMyShow enough time to perform its redirect
    # and populate the showtimes.
    page.wait_for_timeout(8000)

    final_url = page.url
    body_text = page.locator("body").inner_text()

    security_text = (
        "security service to protect itself from online attacks"
    )

    if security_text.lower() in body_text.lower():
        print("BookMyShow security page detected.")
        return False

    target_in_url = f"/{TARGET_DATE}" in final_url

    has_showtimes = (
        "Non-cancellable" in body_text
        or "IMAX Laser" in body_text
    )

    if target_in_url and has_showtimes:
        return True

    if not target_in_url:
        print("NOT OPEN - BookMyShow redirected away.")
    else:
        print("Target date loaded but no showtimes detected.")

    return False


print()
print("BookMyShow Continuous Monitor")
print("=" * 50)
print("Event :", EVENT_CODE)
print("Date  :", TARGET_DATE)
print("URL   :", URL)
print()
print("Schedule:")
print("  :01  :11  :21  :31  :41  :51")
print()
print("Notifications:")
print("  Windows  : enabled")
print(
    "  Telegram :",
    "enabled"
    if os.environ.get("TELEGRAM_BOT_TOKEN")
    and os.environ.get("TELEGRAM_CHAT_ID")
    else "NOT configured",
)
print()
print("Press Ctrl+C to stop.")
print()


try:
    with sync_playwright() as p:

        browser = p.chromium.connect_over_cdp(CDP_URL)

        if not browser.contexts:
            print("ERROR: No Chrome context found.")
            sys.exit(2)

        context = browser.contexts[0]

        if context.pages:
            page = context.pages[0]
        else:
            page = context.new_page()

        print("Connected to your Chrome session.")
        print()

        #
        # IMPORTANT:
        # Wait for the next :01/:11/... boundary instead
        # of checking immediately.
        #
        wait_for_next_check()

        while True:

            try:
                booking_open = check_booking(page)

                if booking_open:

                    print()
                    print("=" * 65)
                    print("BOOKING IS OPEN!")
                    print("=" * 65)
                    print("Event :", EVENT_CODE)
                    print("Date  :", TARGET_DATE)
                    print("URL   :", URL)
                    print("=" * 65)
                    print()

                    send_windows_notification()
                    send_telegram_notification()

                    print()
                    print("Monitoring completed.")
                    print("Exiting.")
                    break

            except Exception as e:
                print("Check failed:", e)

            #
            # Calculate the next actual clock boundary.
            # This avoids drift caused by the 8-second page load.
            #
            wait_for_next_check()


except KeyboardInterrupt:

    print()
    print("Monitor stopped manually.")


except Exception as e:

    print()
    print("ERROR:", e)
    print(
        "Make sure your dedicated Chrome is running "
        "with --remote-debugging-port=9222"
    )
