# GFH Audit Automation

## Original app — exact copy (with all branding)

The untouched original **`GFH_Inventory_Audit_Timesheet.py`** lives at the
repo root, byte-for-byte identical to the source file (logos, icons, themes,
splash screen, stores.json all included):

- `GFH_Inventory_Audit_Timesheet.py` — the original Timesheet Edition app
- `logo_handler.py`, `theme_manager.py`, `header_manager.py` — its branding modules
- `GFH_Telecom_Logo.png`, `gfh_icon.ico`, `stores.json`, `assets/` — logos, window/taskbar icons, embedded base64 assets
- `GFH_Inventory_Audit_Timesheet.spec` — its original PyInstaller recipe → `GFH_Inventory_Audit_Timesheet.exe` (icon: `gfh_icon.ico`)

Run it exactly as before:

```bash
python GFH_Inventory_Audit_Timesheet.py
```

`build_GFH_Audit_Automation.bat` builds **both** executables: the original
branded app first, then the refactored modular app below.

---

## Refactored modular app

Refactored successor of the **GFH Telecom LLC Inventory Audit v27** desktop app.
All WhatsApp Desktop / OS-level UI automation (pyautogui, pywin32, clipboard
pasting, `whatsapp:` URIs) has been **completely removed** — messaging now runs
**100% through WhatsApp Web via Selenium**, with a persistent browser profile so
the QR code is scanned only once.

## Feature Overview

| Area | What it does |
|---|---|
| **WhatsApp Web (Selenium)** | Attaches to your **real Edge window on debug port 9226** (persistent automation profile, extensions visible, window stays open) — group open, text/image send, real `@mention` dropdown selection with `@phone` fallback, continuous message polling |
| **OCR pipeline** | Tesseract OCR + Ghostscript (PDF→PNG) + Pillow preprocessing; extracts 15-digit IMEIs (tolerates OCR-space splits, trailing-12-digit partial matches) |
| **Real-time clearing** | Monitor reads WhatsApp **unread badges** (VidaPay pattern — only opens groups that actually have new messages), parses conversations once per cycle with **BeautifulSoup**, OCRs new images for 15-digit IMEIs, and marks matches **Cleared** with a group confirmation. A persistent processed-image registry makes IMEI scanning **deduplicated** (never re-OCR the same photo, survives restarts) |
| **Portal extraction** | Timesheet (`https://gfh-telecom-app.web.app/timesheet`) and BRS count sheet (`https://wsreports.b2bsoft.com/?platform=brs&performanceapp=0#`) auto-login + download/scrape per district |
| **Employee linking** | `Inventory Audit Status` rep names → `Employee` tab phones → `@phone` tags in every kickoff/variance/reminder message |
| **District scheduling** | Per-district **Audit Start Time** (HH:MM) configured in the UI; the engine queues districts and fires each workflow exactly at its scheduled time |
| **State machine** | `PENDING → KICKOFF → VARIANCE POSTED → REMINDER 1→2→3 → FINAL NOTICE → COMPLETED` (auto-completes early when every variance is cleared) |
| **Reliability** | Driver crash recovery, selector fallbacks, send retries, WAL SQLite with startup backup, full event log |

## District Workflow

1. **Kickoff** — at the district's scheduled start time: fetch Timesheet + Count
   Sheet, compute variances, then post
   *"Audit is open. Please complete the audit within 15 minutes."* tagging the
   active reps.
2. **Variance post** — variance breakdown rendered to PNG and sent to the
   district group with reps tagged via `@phone`.
3. **Reminders** — up to **3** reminders (interval configurable, default 5 min)
   while variances remain unresolved.
4. **Final notice** — after the 3rd reminder the final notice is sent and the
   round is finalized.
5. **Completion** — the audit auto-completes as soon as all variances are
   cleared (via OCR photo matching or manual clearing in the UI).

The engine runs continuously (monitor + scheduler loops) until you press
**STOP**, the schedule window expires, or all districts complete.

## Setup

### Python + packages

```bash
py -3.11 -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
python main.py
```

### Tesseract OCR (IMEI extraction from photos)

- **Windows** — install https://github.com/UB-Mannheim/tesseract/wiki (default
  path auto-detected; a custom path can be set in *Portal Credentials* tab)
- **macOS** — `brew install tesseract`
- **Linux** — `sudo apt install tesseract-ocr`

### Ghostscript (PDF count sheets)

- **Windows** — https://www.ghostscript.com/download.html (`gswin64c` auto-detected)
- **macOS** — `brew install ghostscript`
- **Linux** — `sudo apt install ghostscript`

Both binaries are optional but recommended; without them the app still runs,
it simply cannot OCR photos/PDFs.

### First WhatsApp Web login

Press **START** → the app opens (or attaches to) the automation Edge window on
debug port **9226** → navigate to `web.whatsapp.com` and scan the QR code
**once**. The session persists in the automation profile
(`C:\GFH_Edge_Automation_Profile`); subsequent runs attach to the same window
and log in automatically — identical to the VidaPay Transfer Bot behaviour.

### Edge attach mode (port 9226)

The bot never launches a private throwaway browser: it drives your **real
Edge window** through remote debugging:

- If Edge is not yet running on port `9226`, the app starts it with the
  persistent automation profile and waits for the DevTools port.
- Selenium then attaches via `debuggerAddress=127.0.0.1:9226`, reuses the
  open tabs, keeps extensions visible, and leaves the window open on exit.
- Portal downloads are seeded into the profile so exports land in
  `portal_downloads/` even in attach mode.

Tune it in **Portal Credentials → Reminders & engine**: attach on/off, debug
port, and the automation profile directory.

### How the monitor runs (after pressing START)

1. **START button** starts the engine: scheduler + a continuous monitor loop.
2. Each cycle the monitor reads the WhatsApp Web **notification badges**
   (green unread circles in the chat list, exactly like the VidaPay Transfer
   Bot). WhatsApp notification settings are switched ON automatically once
   per session.
3. Only groups with unread messages are opened; the conversation HTML is
   fetched once and parsed with **BeautifulSoup** (Selenium DOM fallback).
4. New images are downloaded, preprocessed, and OCR'd for 15-digit IMEIs;
   every processed message is recorded in a `processed_images` registry so
   **the same photo/IMEI is never scanned twice** (across restarts too).
5. Matched IMEIs clear their variances and a confirmation is posted to the
   group; unmatched photos are logged once and skipped afterwards.

## Configuration

Everything is configured in the GUI and stored in `gfh_audit_config.json`
(next to the app; passwords base64-obfuscated, file excluded from git):

- **Portal Credentials tab** — Timesheet email/password, BRS email/password,
  reminder interval, audit timeout, poll interval, browser choice,
  Tesseract/Ghostscript paths. Each portal has a **Test login** button.
- **Districts & Schedule tab** — District, WhatsApp group name, Audit Start
  Time (`HH:MM`), Enabled.
- **Employees tab** — employee names + phone numbers (+ district). Import from
  the Employee Time Sheet workbook or an xlsx/CSV with
  `Employee Name` / `Phone Number` headers.

Manual loading of `Inventory_Count_Result_Details.xlsx` and
`Employee_Time_Sheet.xlsx` remains available at the top of the window — handy
when portals are down; portal data and manual data are cross-referenced by the
same variance engine.

## Project Layout

```
gfh_audit/
├── config.py               # AppConfig + obfuscated credential storage
├── database.py             # SQLite/WAL: variances, employees, runs, events
├── pipeline.py             # store maps, latest-count filter, variance extraction
├── renderer.py             # variance batch → PNG
├── textutils.py            # normalizers, phone/mention, excel serials
├── xlsx_reader.py          # robust raw-XML xlsx reader
├── engine/
│   ├── audit_engine.py     # orchestrator (portals → variances → WhatsApp → FSM)
│   ├── monitor.py          # WhatsApp image watcher → OCR → auto-clear
│   ├── scheduler.py        # per-district start-time queue
│   └── state_machine.py    # district audit FSM
├── ocr/engine.py           # Ghostscript + Pillow + Tesseract + IMEI matcher
├── scrapers/               # timesheet + BRS portal scrapers
├── whatsapp/
│   ├── driver_manager.py   # Selenium lifecycle + persistent profile
│   ├── mentions.py         # rep → @phone resolution
│   └── whatsapp_web.py     # session, groups, send, poll, blob image download
└── gui/app.py              # tkinter app (6 tabs, Start/Stop)
tests/                      # 53 unit tests
```

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Build the Windows EXE (one-click)

Run **`build_GFH_Audit_Automation.bat`** (double-click it, or execute it from
a terminal). On every run it:

1. Verifies Python, PyInstaller (auto-installs) and Git are on `PATH`
2. Clones this repo to `C:\Users\AbadUmairChanna\Downloads\GitHub\GFH-Audit-Automation`
   — or `git pull`s the latest code if it already exists
3. Cleans `build/` / `dist/` / `__pycache__`, redirects the PyInstaller
   workpath to `%TEMP%\pyi_build\GFH_Audit_Automation`
4. Installs `requirements.txt`
5. Builds with the committed `GFH_Audit_Automation.spec` (single-file, windowed)
6. Copies the result to `C:\Users\AbadUmairChanna\Downloads\GitHub\GFH_Audit_Automation.exe`

The produced EXE is portable: its data files (SQLite DB, logs, config,
`portal_downloads/`) are created **next to the exe** on first launch. The
Edge window it attaches to uses the dedicated profile
`C:\GFH_Edge_Automation_Profile` (WhatsApp QR scan + portal logins persist
there). For OCR photo matching, Tesseract OCR + Ghostscript must be installed
on the machine (both auto-detected at runtime).
