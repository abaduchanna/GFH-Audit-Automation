# GFH Inventory Audit

GFH Telecom inventory count audit automation for VidaPay stores — verifies store counts, renders the district-colored status image, and reports via WhatsApp. Includes the Timesheet cross-check variant.

## Apps

| File | Purpose |
|------|---------|
| `GFH_Inventory_Audit.py` | Inventory count audit — status image, WhatsApp summary |
| `GFH_Inventory_Audit_Timesheet.py` | Timesheet cross-check — per-store employee matching, plain count reminders |

## Build

- **Locally:** run `build_GFH_Inventory_Audit.bat` or `build_GFH_Inventory_Audit_Timesheet.bat` — each force-syncs this repo from GitHub (self-heals origin, resets to `origin/main`), then builds the exe with PyInstaller into `C:\Users\AbadUmairChanna\Downloads\GitHub`.
- **CI:** every push to `main` builds both exes on GitHub Actions and publishes a **GFH Build N** release with the exes attached.

## Support modules

- `logo_handler.py` — GFH Telecom logo/icon loading (shared)
- `theme_manager.py` — dark theme helpers (shared)
- `assets/`, `gfh_icon.ico`, `GFH_Telecom_Logo.png`, `stores.json` — bundled resources referenced by both `.spec` files
