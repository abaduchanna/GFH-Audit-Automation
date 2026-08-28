"""OCR pipeline: Ghostscript (PDF rasterising) + Pillow preprocessing +
Tesseract text extraction + 15-digit IMEI harvesting."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from PIL import Image, ImageFilter, ImageOps

from ..paths import OCR_DEBUG_DIR

logger = logging.getLogger("gfh.audit.ocr")


def find_ghostscript(configured: str = "") -> Optional[str]:
    """Locate a Ghostscript executable (gs on POSIX, gswin64c on Windows)."""
    candidates = []
    if configured:
        candidates.append(configured)
    candidates.extend(["gs", "gswin64c", "gswin32c"])
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    # common Windows install locations
    for pattern in (
        r"C:\Program Files\gs\gs*\bin\gswin64c.exe",
        r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe",
    ):
        try:
            import glob

            matches = glob.glob(pattern)
            if matches:
                return sorted(matches)[-1]
        except Exception:
            continue
    return None


def find_tesseract(configured: str = "") -> Optional[str]:
    """Locate a Tesseract executable."""
    candidates = []
    if configured:
        candidates.append(configured)
    candidates.extend(["tesseract"])
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    for pattern in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Tesseract-OCR\tesseract.exe",
    ):
        try:
            import glob

            matches = glob.glob(pattern)
            if matches:
                return sorted(matches)[-1]
        except Exception:
            continue
    return None


def pdf_to_images(pdf_path: Path, gs_path: str, dpi: int = 300) -> List[Path]:
    """Rasterise every PDF page to PNG via Ghostscript."""
    out_dir = Path(tempfile.mkdtemp(prefix="gfh_ocr_pdf_"))
    output_template = str(out_dir / "page-%03d.png")
    cmd = [
        gs_path,
        "-dNOPAUSE", "-dBATCH", "-dSAFER",
        f"-sDEVICE=png16m", f"-r{dpi}",
        f"-sOutputFile={output_template}",
        str(pdf_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"Ghostscript failed ({result.returncode}): {result.stderr.decode(errors='ignore')[:400]}"
        )
    pages = sorted(out_dir.glob("page-*.png"))
    return pages


def preprocess_image(image: Image.Image) -> Image.Image:
    """Grayscale → autocontrast → upscale → sharpen → binarise.

    Makes low-resolution WhatsApp photos readable by Tesseract."""
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    image = image.convert("L")
    image = ImageOps.autocontrast(image, cutoff=2)
    width, height = image.size
    if width < 1600:
        scale = min(3.0, 1600 / max(1, width))
        image = image.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
    image = image.filter(ImageFilter.SHARPEN)
    image = image.point(lambda p: 255 if p > 150 else 0)  # binarise
    return image


@dataclass
class OcrResult:
    source: str
    text: str
    imeis: List[str]
    word_count: int = 0
    ok: bool = True
    error: str = ""


class OcrEngine:
    """Tesseract OCR with Ghostscript support for PDF sources."""

    IMEI_RE = re.compile(r"(?<!\d)\d{15}(?!\d)")

    def __init__(self, tesseract_path: str = "", ghostscript_path: str = "", language: str = "eng"):
        self.tesseract_path = find_tesseract(tesseract_path)
        self.ghostscript_path = find_ghostscript(ghostscript_path)
        self.language = language or "eng"
        if self.tesseract_path:
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
        logger.info(
            "OCR engine ready: tesseract=%s ghostscript=%s",
            self.tesseract_path or "MISSING",
            self.ghostscript_path or "MISSING",
        )

    @property
    def available(self) -> bool:
        return bool(self.tesseract_path)

    # -- extraction -----------------------------------------------------------
    def extract_text_from_image(self, image: Image.Image, source_name: str = "image") -> OcrResult:
        if not self.available:
            return OcrResult(source=source_name, text="", imeis=[], ok=False,
                             error="Tesseract OCR not installed or not found on PATH")
        try:
            processed = preprocess_image(image)
            self._save_debug(processed, source_name)
            import pytesseract

            text = pytesseract.image_to_string(
                processed, lang=self.language, config="--psm 6"
            )
            return OcrResult(
                source=source_name,
                text=text,
                imeis=self.extract_imeis(text),
                word_count=len(text.split()),
            )
        except Exception as exc:
            logger.error("OCR failed on %s: %s", source_name, exc)
            return OcrResult(source=source_name, text="", imeis=[], ok=False, error=str(exc))

    def extract_from_file(self, file_path: Path) -> OcrResult:
        file_path = Path(file_path)
        suffix = file_path.suffix.lower()
        try:
            if suffix == ".pdf":
                if not self.ghostscript_path:
                    return OcrResult(source=file_path.name, text="", imeis=[], ok=False,
                                     error="Ghostscript not installed (needed for PDF)")
                combined_text: List[str] = []
                all_imeis: List[str] = []
                for page in pdf_to_images(file_path, self.ghostscript_path):
                    with Image.open(page) as img:
                        result = self.extract_text_from_image(img, f"{file_path.name} :: {page.name}")
                        combined_text.append(result.text)
                        all_imeis.extend(result.imeis)
                    try:
                        page.unlink()
                    except Exception:
                        pass
                try:
                    page.parent.rmdir()
                except Exception:
                    pass
                return OcrResult(
                    source=file_path.name, text="\n".join(combined_text),
                    imeis=self.extract_imeis("\n".join(combined_text)),
                )
            with Image.open(file_path) as img:
                return self.extract_text_from_image(img, file_path.name)
        except Exception as exc:
            return OcrResult(source=file_path.name, text="", imeis=[], ok=False, error=str(exc))

    def extract_from_bytes(self, data: bytes, source_name: str = "whatsapp_image") -> OcrResult:
        import io

        try:
            with Image.open(io.BytesIO(data)) as img:
                return self.extract_text_from_image(img, source_name)
        except Exception as exc:
            return OcrResult(source=source_name, text="", imeis=[], ok=False, error=str(exc))

    # -- IMEI utilities ----------------------------------------------------------
    @classmethod
    def extract_imeis(cls, text: str) -> List[str]:
        """All 15-digit candidates, deduplicated, order preserved.

        Also rescues 15-digit IMEIs that OCR split with stray spaces every
        3-4 digits (e.g. '356 938 035 194 802')."""
        if not text:
            return []
        found: List[str] = []
        seen = set()

        def _add(candidate: str) -> None:
            digits = re.sub(r"\D", "", candidate)
            if len(digits) == 15 and digits not in seen:
                seen.add(digits)
                found.append(digits)

        for match in cls.IMEI_RE.finditer(text):
            _add(match.group(0))

        # re-join space-separated groups totalling 15 digits; the lookarounds
        # prevent partial reads of longer pure digit runs (e.g. 16-digit).
        for match in re.finditer(r"(?<!\d)(?:(?:\d[ \-]?){14,22}\d)(?!\d)", text):
            _add(match.group(0))

        return found

    @staticmethod
    def luhn_valid(imei: str) -> bool:
        digits = re.sub(r"\D", "", imei or "")
        if len(digits) != 15:
            return False
        total = 0
        for i, ch in enumerate(reversed(digits)):
            d = int(ch)
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10 == 0

    @staticmethod
    def _save_debug(image: Image.Image, source_name: str) -> None:
        try:
            OCR_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            import datetime as dt
            import re as _re

            safe = _re.sub(r"[^A-Za-z0-9_-]+", "_", source_name)[:50] or "image"
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            image.save(OCR_DEBUG_DIR / f"{stamp}_{safe}.png")
        except Exception:
            pass


def match_imei_against_variances(imei: str, pending_variances: Iterable) -> List:
    """Return pending variance rows whose IMEI matches the OCR read.

    Tolerates partial OCR reads: a match also counts when the OCR digits or
    the stored IMEI end with at least the trailing 12 digits of the other."""
    digits = re.sub(r"\D", "", imei or "")
    if len(digits) < 11:
        return []
    matches = []
    for row in pending_variances:
        candidate = re.sub(r"\D", "", getattr(row, "imei", "") or "")
        if not candidate:
            continue
        if candidate == digits:
            matches.append(row)
        elif len(digits) >= 12 and candidate.endswith(digits[-12:]):
            matches.append(row)
        elif len(candidate) >= 12 and digits.endswith(candidate[-12:]):
            matches.append(row)
    return matches
