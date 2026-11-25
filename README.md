# BookMyShow Availability Monitor

A small Python project for monitoring BookMyShow booking availability using an existing local Chrome session.

The project started as a personal experiment to learn about:

- Playwright and Chrome DevTools Protocol (CDP)
- Telegram Bot API integration
- Scheduled polling
- Multi-user bot design
- Windows notifications
- Browser session reuse
- Handling websites that behave differently for normal browsers and automated HTTP clients

> This project is intended for personal learning and lightweight availability monitoring. It does not attempt to bypass CAPTCHAs, security challenges, access controls, or anti-bot protections.

---

## How it works

Instead of launching a new automated browser, the monitor connects to a Chrome instance that you start yourself with Chrome remote debugging enabled.

```text
Telegram
   |
   | /monitor ET00523959 20261128
   v
Python Monitor
   |
   v
Existing Chrome via CDP (:9222)
   |
   v
BookMyShow
   |
   +--> Date redirected / no shows
   |       |
   |       +--> keep monitoring
   |
   +--> Target date + showtimes detected
           |
           +--> Windows notification
           |
           +--> Telegram notification
           |
           +--> stop that monitor
```

The monitor checks availability at:

```text
HH:01
HH:11
HH:21
HH:31
HH:41
HH:51
```

This uses fixed wall-clock checkpoints rather than repeatedly sleeping for 10 minutes, which avoids schedule drift.

---

# Features

## Local single-user monitor

`bms_monitor_local.py`

Useful when you only want to monitor one booking from the command line.

Example:

```powershell
py bms_monitor_local.py ET00523959 20261128
```

It supports:

- Event code + target date input
- Existing Chrome session through CDP
- Fixed 10-minute monitoring schedule
- Windows notification
- Telegram notification
- Automatic stop when booking is detected

---

## Multi-user Telegram bot

`bms_monitor_multiuser.py`

Turns the monitor into a Telegram-controlled application.

Supported commands:

```text
/monitor EVENT_CODE YYYYMMDD
/list
/status
/stop EVENT_CODE YYYYMMDD
/stopall
/help
```

Example:

```text
/monitor ET00523959 20261128
```

The bot immediately queues the monitor and subsequently checks at:

```text
:01 :11 :21 :31 :41 :51
```

Each Telegram user has independent monitors.

Default limits:

```text
Maximum monitors per user: 3
Maximum monitors total:    10
```

BookMyShow checks are performed sequentially through the same Chrome session rather than navigating the browser concurrently.

---

# Requirements

- Windows
- Python 3
- Google Chrome
- Playwright
- Plyer
- Telegram bot, if Telegram integration is used

Install Python dependencies:

```powershell
py -m pip install -r requirements.txt
```

Install Playwright's Chromium support if needed:

```powershell
py -m playwright install chromium
```

Note that the monitor itself connects to your existing Chrome instance rather than relying on Playwright's bundled Chromium for BookMyShow navigation.

---

# Chrome setup

Close the dedicated monitoring Chrome instance if one is already running.

Start Chrome with remote debugging enabled:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:TEMP\bms-chrome-profile"
```

This opens a dedicated Chrome profile.

Open BookMyShow manually in this Chrome window once and verify that the site loads normally.

The Python monitor connects to:

```text
http://127.0.0.1:9222
```

Do not expose port `9222` to the Internet.

---

# Telegram bot setup

## 1. Create a bot

In Telegram, open `@BotFather`.

Send:

```text
/newbot
```

Follow the prompts and keep the generated bot token private.

Do not commit the bot token to this repository.

---

## 2. Get your Chat ID

Open your new bot in Telegram and send:

```text
/start
```

Then inspect:

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

Find:

```json
"chat": {
    "id": 123456789
}
```

The numeric value is your Telegram Chat ID.

---

# Environment variables

Set the Telegram bot token in PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN"
```

For the single-user script:

```powershell
$env:TELEGRAM_CHAT_ID="YOUR_CHAT_ID"
```

Do not hard-code these values in Python.

---

# Multi-user access control

The multi-user bot supports an optional Chat ID allowlist.

For your own private bot:

```powershell
$env:ALLOWED_CHAT_IDS="123456789"
```

For several trusted users:

```powershell
$env:ALLOWED_CHAT_IDS="123456789,987654321,456789123"
```

Then start:

```powershell
py bms_monitor_multiuser.py
```

If `ALLOWED_CHAT_IDS` is configured, requests from other Telegram accounts are rejected.

If `ALLOWED_CHAT_IDS` is not configured, the bot operates in public mode.

Public mode is not recommended unless the machine running the bot is intentionally being used for that purpose.

Never give users your Telegram BotFather token. Users only need the bot username.

---

# Using the multi-user bot

Start the application:

```powershell
py bms_monitor_multiuser.py
```

Then use Telegram.

### Start monitoring

```text
/monitor ET00523959 20261128
```

### View monitors

```text
/list
```

or:

```text
/status
```

### Stop one monitor

```text
/stop ET00523959 20261128
```

### Stop all your monitors

```text
/stopall
```

### Help

```text
/help
```

---

# Optional configuration

The default example is configured for:

```text
City:     coimbatore
Movie:    ramayana
Language: tamil
```

The multi-user script supports environment-variable overrides.

Example:

```powershell
$env:BMS_CITY="coimbatore"
$env:BMS_MOVIE_SLUG="ramayana"
$env:BMS_LANGUAGE="tamil"
```

Chrome's CDP endpoint can also be overridden:

```powershell
$env:BMS_CDP_URL="http://127.0.0.1:9222"
```

---

# Availability detection

The monitor requests the target booking URL in the existing browser session.

Conceptually:

```text
requested target date
        |
        v
BookMyShow redirects away?
        |
       yes
        |
        +--> booking not open

        no
        |
        v
showtime content detected?
        |
       yes
        |
        +--> booking open
```

The current implementation considers the booking available when:

1. BookMyShow remains on the requested target date, and
2. expected showtime content is present.

If BookMyShow redirects to another date, the monitor considers the requested date unavailable.

---

# Why use an existing Chrome session?

During development, direct HTTP requests and fresh automated browser sessions did not behave the same way as a normal interactive Chrome session.

This project therefore uses an existing user-controlled Chrome session via the Chrome DevTools Protocol.

It does not attempt to defeat or disguise automation against CAPTCHA, browser security challenges, or anti-bot protection.

If the target website displays a security challenge, the monitor should treat that check as unsuccessful rather than attempting to bypass it.

---

# Important limitations

## The computer must stay awake

The monitor is a local process.

It works when:

```text
Windows locked       YES
Display turned off   YES
Windows awake        YES
```

It does not continue checking while the computer is asleep, hibernating, or powered off.

---

## Chrome must remain running

The dedicated Chrome instance on port `9222` must remain available while monitoring.

Closing it will cause checks to fail.

---

## Website changes

BookMyShow can change:

- URLs
- page structure
- showtime labels
- redirect behavior
- security behavior

Those changes may require updating the detection logic.

---

## Monitoring is sequential

The multi-user version intentionally processes BookMyShow monitors sequentially through one Chrome tab.

It does not perform parallel browser navigation.

For example, with an 8-second page-load allowance:

```text
1 monitor   ~= 8 seconds/check cycle
5 monitors  ~= 40+ seconds/check cycle
10 monitors ~= 80+ seconds/check cycle
```

This is why the default implementation limits total active monitors.

---

# Security

Never commit:

```text
Telegram bot tokens
Telegram chat IDs you consider private
Chrome cookies
Chrome profile data
session tokens
.env files
corporate proxy credentials
certificates
```

Recommended `.gitignore`:

```gitignore
.env
.env.*
__pycache__/
*.pyc
*.pyo
.vscode/
.idea/
chrome-*rofile/
monitor-state.json
```

Th**Chrome debugging endpoint should r*main*bound to and accessed through the *ocal machine.

Do not expose Chrom* remote debugging directly to the *nternet.

---

# Repository layout*
```text
*ookmyshow-monitor/
|
|-- bms_monit*r*local.py
|-- bms_monitor_multiuser*py
|-- requirements.txt
|-- README*md
`-- .gitignore
*``

---

# Example workflow

Start*Chrome:

```powershell
& "C:\Progr*m Files\Google\Chrome\Application\*hrome.exe" --remote-debugging-port*9222 --user-data-dir="$env:TEMP\bm*-chrome-profile"
```

Set Telegram*configuration:

```powershell
$env*TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN*
$env:ALLOWED_CHAT_IDS="YOUR_CHAT_*D"
```

Start the bot:

```powersh*ll
py bms_monitor_multiuser.py
```*
Lock the computer if desired:

``*text
Win + L
```

Then from Telegr*m:

```text
/monitor ET00523959 20*61128
```

Later:

```text
/status*```

When availability is detected* the requester receives the BookMy*how URL through Telegram and that *ndividual monitor is automatically*removed.

The Python bot remains r*nning and can accept additional mo*itor requests.

---

# Disclaimer
*This repository is an educational *roject and is not affiliated with *r endorsed by BookMyShow or Telegr*m.

Use it responsibly and in acco*dance with the relevant websites' *erms, acceptable-use requirements,*and local policies.

The project i*tentionally does not include mecha*isms for bypassing CAPTCHA, securi*y challenges, authentication contr*ls, or other website protections.
*
