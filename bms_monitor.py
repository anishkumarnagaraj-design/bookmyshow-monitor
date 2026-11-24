import sys
from datetime import datetime
from playwright.sync_api import sync_playwright

if len(sys.argv) != 3:
    print("Usage: python bms_monitor.py EVENT_CODE YYYYMMDD")
    print("Example: python bms_monitor.py ET00523959 20261128")
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

print("BookMyShow Monitor")
print("------------------------------")
print("Event :", EVENT_CODE)
print("Date  :", TARGET_DATE)
print("URL   :", URL)
print()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] Checking BookMyShow...")

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        # Allow BookMyShow JavaScript content to load
        page.wait_for_timeout(8000)

        body_text = page.locator("body").inner_text()

        if "Non-cancellable" in body_text:
            print()
            print("BOOKING_OPEN=true")
            print("Booking/show listings detected.")
            print(URL)
            sys.exit(0)

        print()
        print("BOOKING_OPEN=false")
        print("Booking is not open yet.")
        sys.exit(1)

    except Exception as e:
        print()
        print("BOOKING_OPEN=error")
        print("Check failed:", e)
        sys.exit(2)

    finally:
        page.close()
        browser.close()
