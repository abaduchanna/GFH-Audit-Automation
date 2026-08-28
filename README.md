# GFH Audit Automation

Refactored successor of the **GFH Telecom LLC Inventory Audit v27** desktop app.
All WhatsApp Desktop / OS-level UI automation (pyautogui, pywin32, clipboard
pasting, `whatsapp:` URIs) has been **completely removed** — messaging now runs
**100% through WhatsApp Web via Selenium**, with a persistent browser profile so
the QR code is scanned only once.

## Feature Overview

| Area | What it does |
|---|---|
| **WhatsApp Web (Selenium)** | Persistent Chrome/Edge profile (`whatsapp_web_profile/`), group open, text/image send, real `@mention` dropdown selection with `@phone` fallback, continuous message polling |
| **OCR pipeline** | Tesseract OCR + Ghostscript (PDF→PNG) + Pillow preprocessing; extracts 15-digit IMEIs (tolerates OCR-space splits, trailing-12-digit partial matches) |
| **Real-time clearing** | Incoming group photos are OCR'd automatically; matched IMEIs are marked **Cleared** in the audit tracker and a ✅ confirmation is posted to the group |
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

Press **START** → the automated browser opens `web.whatsapp.com` → scan the QR
code **once**. The session persists in the profile directory; subsequent runs
log in automatically.

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

Run **`build_exe.bat`** (double-click it, or execute it from a terminal). On
every run it:

1. Verifies Git and Python 3.10+ are on `PATH` (with install links if missing)
2. Clones this repo to `%USERPROFILE%\GFH-Audit-Automation` — or `git pull`s
   the latest code if the folder already exists (hard-reset fallback keeps it
   always in sync)
3. Creates/updates a `.venv`, installs `requirements.txt` + PyInstaller
4. Warns about optional runtime tools (Tesseract, Ghostscript, Chrome)
5. Rebuilds `dist\GFHAuditAutomation.exe` from scratch with the committed
   `gfh_audit.spec` (single-file, windowed)
6. Saves a dated copy to `releases\GFHAuditAutomation_YYYYMMDD_HHMMSS.exe`

Override the workspace folder or source repo before running:

```bat
set GFH_WORKDIR=D:\GFH-Audit-Automation
set GFH_REPO_URL=https://github.com/you/your-fork.git
build_exe.bat
```

The produced EXE is portable: its data files (SQLite DB, WhatsApp profile,
logs, config) are created **next to the exe** on first launch. The machine
that runs it still needs Google Chrome, and — for OCR photo matching —
Tesseract OCR + Ghostscript (both auto-detected, links in the warnings).
