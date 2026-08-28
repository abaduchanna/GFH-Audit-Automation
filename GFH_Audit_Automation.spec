# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for GFH Audit Automation.

Produces a single-file, windowed (no console) executable:

    pyinstaller --noconfirm --clean gfh_audit.spec

Output:  dist/GFH_Audit_Automation.exe

Notes
-----
* Runtime data (sqlite DB, logs, whatsapp profile, config JSON) is written
  NEXT TO the executable, not inside it - the exe stays portable.
* Tesseract / Ghostscript / Google Chrome are detected on the target machine
  at runtime (see gfh_audit/ocr/engine.py and README) and are NOT bundled.
"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

datas = [
    # Example config ships with the exe; the live config lives next to the exe.
    ("config/config.example.json", "config"),
    # GFH branding — navy header logo + window/taskbar icon (same as original)
    ("GFH_Telecom_Logo.png", "."),
    ("gfh_icon.ico", "."),
]

# Selenium ships the Selenium-Manager binary and remote JS - bundle them so
# chromedriver resolution keeps working from the frozen exe.
datas += collect_data_files("selenium")

hiddenimports = collect_submodules("gfh_audit")  # lazy imports in scrapers/engine
hiddenimports += [
    "PIL._tkinter_finder",
    "bs4",
    "soupsieve",
    "pytesseract",
    "openpyxl",
    "selenium",
    # GFH ecosystem branding modules (repo root, imported by gfh_audit.gui.app)
    "theme_manager",
    "logo_handler",
    "header_manager",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "pandas",
        "IPython",
        "pytest",
        "tkinter.test",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GFH_Audit_Automation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="gfh_icon.ico",
)
