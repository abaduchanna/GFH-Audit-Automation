#!/usr/bin/env python3
"""GFH Audit Automation — application entry point.

Usage:
    py main.py            (or python3 main.py)

Requirements on PATH (auto-detected, configurable in the UI):
    • Tesseract OCR  — IMEI extraction from photos
    • Ghostscript    — PDF rasterising for OCR
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gfh_audit.gui import run_gui  # noqa: E402

if __name__ == "__main__":
    run_gui()
