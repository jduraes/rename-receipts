#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import re
import shutil
from pathlib import Path
from dateutil import parser as date_parser

SCRIPT_DIR = Path(__file__).resolve().parent
VERSION = "1.0.1"

STATS = {
    "scanned": 0,
    "renamed": 0,
    "skipped_already_named": 0,
    "skipped_originals": 0,
    "skipped_internal": 0,
}

# Optional dependencies
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from PIL import Image
    import pytesseract
except ImportError:
    Image = None
    pytesseract = None

# Try to auto-detect Tesseract on Windows so OCR works out of the box.
if pytesseract is not None:
    possible_paths = [
        r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
        r"C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe",
    ]
    for p in possible_paths:
        if Path(p).exists():
            pytesseract.pytesseract.tesseract_cmd = p
            break


DEFAULT_CONFIG = {
    "expense_types": {
        "train": ["train", "rail", "bahn", "sncf", "trenitalia"],
        "parking": ["parking", "parkhaus", "garage", "parc auto"],
        "hotel": ["hotel", "motel", "hostel", "inn"],
        "taxi": ["taxi", "cab", "uber", "lyft", "bolt"],
        "bus": ["bus"],
        "tram": ["tram", "streetcar", "tramway"],
        "breakfast": ["breakfast", "petit dejeuner", "colazione"],
        "lunch": ["lunch", "mittagessen", "pranzo"],
        "dinner": ["dinner", "abendessen", "cena"],
    },
    # Provider-to-expense-type mapping, grown over time.
    # Keys are lowercase provider keywords found in text/filename.
    "providers": {
        "uber": "taxi",
        "apcoa": "parking",
        "ringgo": "parking",
        "gwr": "train",
        "great western railway": "train",
        "kfc": "other",
    },
    # Keywords that indicate this is a meal/restaurant receipt,
    # so time-of-day can decide breakfast/lunch/dinner.
    "meal_keywords": [
        "restaurant", "ristorante", "cafe", "cafè", "coffee",
        "bar", "bistro", "ristorante", "food", "meal", "snack",
    ],
    # Time buckets for meals (24h, inclusive bounds)
    "meal_time_buckets": {
        "breakfast": {"start": "04:00", "end": "10:59"},
        "lunch": {"start": "11:00", "end": "16:59"},
        "dinner": {"start": "17:00", "end": "03:59"},
    },
    "fallback_type": "other",
}


def load_config(base_dir: Path) -> dict:
    # Config is always stored next to the script, not in the scan base_dir.
    cfg_path = SCRIPT_DIR / "expense_config.json"
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = DEFAULT_CONFIG
    # Ensure required keys exist
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    save_config(base_dir, cfg)
    return cfg


def save_config(base_dir: Path, cfg: dict) -> None:
    # Config is always stored next to the script, not in the scan base_dir.
    cfg_path = SCRIPT_DIR / "expense_config.json"
    with cfg_path.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def is_under_originals(base_dir: Path, path: Path) -> bool:
    try:
        path.relative_to(base_dir / "originals")
        return True
    except ValueError:
        return False


# Filenames that already match our target pattern should not be renamed again.
RENAMED_BASENAME_RE = re.compile(
    r"^(?P<date>\d{2}\.\d{2}\.\d{4}(?:\[\])?)-(?P<type>[a-z]+)-(?P<vendor>.+)$",
    re.IGNORECASE,
)


def already_in_target_format(path: Path) -> bool:
    m = RENAMED_BASENAME_RE.match(path.stem)
    if not m:
        return False
    vendor = m.group("vendor")
    v_lower = vendor.lower()
    # Exactly 'unknown' is considered final and should never be changed.
    if v_lower == "unknown":
        return True
    # 'unknown-*' is not final; we want to normalize those to 'Unknown'.
    if v_lower.startswith("unknown-"):
        return False
    # For other vendors, if the vendor itself ends with -digits, treat as not-final so we can clean it.
    if re.search(r"-\d+$", vendor):
        return False
    return True


def backup_file(base_dir: Path, path: Path) -> None:
    if is_under_originals(base_dir, path):
        return
    rel = path.relative_to(base_dir)
    backup = base_dir / "originals" / rel
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copy2(path, backup)


def extract_text_from_pdf(path: Path) -> str:
    if pdfplumber is None:
        return ""
    try:
        texts: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:10]:  # limit pages for speed
                txt = page.extract_text() or ""
                if not txt.strip() and hasattr(page, "to_image") and Image is not None and pytesseract is not None:
                    # Likely an image-based PDF with no text layer; use OCR on rendered image
                    try:
                        pil_img = page.to_image(resolution=300).original
                        ocr_txt = pytesseract.image_to_string(pil_img)
                        txt = ocr_txt or ""
                    except Exception:
                        pass
                if txt:
                    texts.append(txt)
        return "\n".join(texts)
    except Exception:
        return ""


def extract_text_from_image(path: Path) -> str:
    if Image is None or pytesseract is None:
        return ""
    try:
        img = Image.open(str(path))
        return pytesseract.image_to_string(img)
    except Exception:
        return ""


def extract_text_from_textlike(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            return f.read(50000)  # limit size
    except Exception:
        return ""


def strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def extract_text_for_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(path)
    elif ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".heic"}:
        return extract_text_from_image(path)
    elif ext in {".txt", ".log", ".csv"}:
        return extract_text_from_textlike(path)
    elif ext in {".htm", ".html"}:
        raw = extract_text_from_textlike(path)
        return strip_html_tags(raw)
    else:
        # Unknown binary: best we can do is filename-based heuristics
        return ""


# Regex patterns to extract candidate date strings.
DATE_TEXT_PATTERNS = [
    # 01/11/2025, 1.11.25, 05-11-2025
    r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",
    # 2025-11-01
    r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b",
    # 8-digit compact: 20251105 or 05112025
    r"\b\d{8}\b",
    # Textual months: 05 Nov 2025, 5 November 25
    r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\b",
    # Textual months alternative: Nov 05 2025, November 5, 2025
    r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}\b",
    # Day + textual month without year (e.g. 26 Nov)
    r"\b\d{1,2}\s+[A-Za-z]{3,9}\b",
]


def normalize_year(y: int) -> int:
    """Normalize 2-digit years to 2000-2099."""
    if y < 100:
        return 2000 + y
    return y


def extract_dates_from_text(text: str) -> list[dt.date]:
    """Return all plausible dates from text, picking up many formats.

    Uses regex to find candidate substrings, then python-dateutil to parse
    with day-first semantics. Returns unique dates sorted ascending.

    Only keeps dates within a sliding window: [today.year-3, today.year+1].
    This avoids picking company founding dates from letterheads etc.
    """
    snippet = text[:8000]
    candidates: set[str] = set()
    for pat in DATE_TEXT_PATTERNS:
        for m in re.finditer(pat, snippet):
            candidates.add(m.group(0))

    today = dt.date.today()
    min_year = today.year - 3
    max_year = today.year + 1

    dates: set[dt.date] = set()
    for s in candidates:
        try:
            dt_obj = date_parser.parse(s, dayfirst=True, yearfirst=False, fuzzy=True)
            y = normalize_year(dt_obj.year)
            if not (min_year <= y <= max_year):
                continue
            dates.add(dt.date(y, dt_obj.month, dt_obj.day))
        except Exception:
            continue

    return sorted(dates)


def extract_date_from_filename(name: str, fallback_year: int | None = None) -> dt.date | None:
    """Extract a date from filename, supporting many numeric formats.

    - YYYYMMDD, DDMMYYYY, DDMMYY
    - DD/MM[/YY], DD-MM, etc.
    If only day+month is present, use fallback_year if provided.
    """
    base = name.rsplit(".", 1)[0]

    # 1) Compact 8-digit: try YYYYMMDD then DDMMYYYY (allow trailing time/underscores).
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", base)
    if m:
        s = m.group(1)
        y1, m1, d1 = int(s[0:4]), int(s[4:6]), int(s[6:8])
        y2, m2, d2 = int(s[4:8]), int(s[2:4]), int(s[0:2])
        for (y, mo, d) in [(y1, m1, d1), (y2, m2, d2)]:
            try:
                return dt.date(normalize_year(y), mo, d)
            except ValueError:
                continue

    # 2) Separated with year: reuse dateutil on candidate substrings.
    dates_from_text = extract_dates_from_text(base)
    if dates_from_text:
        return dates_from_text[0]

    # 3) Day+month without year: use fallback_year if available.
    m2 = re.search(r"\b(\d{1,2})[./-](\d{1,2})\b", base)
    if m2 and fallback_year is not None:
        d, mo = int(m2.group(1)), int(m2.group(2))
        try:
            return dt.date(fallback_year, mo, d)
        except ValueError:
            return None

    return None


def extract_time_from_text(text: str) -> dt.time | None:
    # Simple HH:MM (ignore seconds)
    for m in re.finditer(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\b", text[:8000]):
        h = int(m.group("h"))
        mi = int(m.group("m"))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return dt.time(hour=h, minute=mi)
    return None


def parse_time_str(t: str) -> dt.time:
    h, m = map(int, t.split(":", 1))
    return dt.time(h, m)


def time_in_range(t: dt.time, start: dt.time, end: dt.time) -> bool:
    if start <= end:
        return start <= t <= end
    # Over midnight (not used here but safe)
    return t >= start or t <= end


def looks_like_meal(text_lc: str, name_lc: str, cfg: dict) -> bool:
    for kw in cfg.get("meal_keywords", []):
        if kw.lower() in text_lc or kw.lower() in name_lc:
            return True
    return False


def infer_meal_type_by_time(tx_time: dt.time | None, cfg: dict) -> str | None:
    if tx_time is None:
        return None
    buckets = cfg.get("meal_time_buckets", {})
    for tname, rng in buckets.items():
        try:
            start = parse_time_str(rng["start"])
            end = parse_time_str(rng["end"])
        except Exception:
            continue
        if time_in_range(tx_time, start, end):
            return tname
    return None


def detect_provider(text_lc: str, name_lc: str, cfg: dict) -> str | None:
    """Return provider keyword if any known provider is found in text or filename."""
    providers = cfg.get("providers", {})
    for prov in providers.keys():
        p = prov.lower()
        if p and (p in text_lc or p in name_lc):
            return p
    return None


def infer_expense_type(text: str, filename: str, tx_time: dt.time | None, cfg: dict) -> str | None:
    text_lc = text.lower()
    name_lc = filename.lower()

    # 0) Provider-based classification first (e.g. Uber -> taxi, APCOA/RingGo -> parking).
    provider_key = detect_provider(text_lc, name_lc, cfg)
    if provider_key is not None:
        etype = cfg.get("providers", {}).get(provider_key)
        if etype:
            return etype

    # 1) If this looks like a meal, use time-of-day for breakfast/lunch/dinner
    if looks_like_meal(text_lc, name_lc, cfg):
        meal_type = infer_meal_type_by_time(tx_time, cfg)
        if meal_type:
            return meal_type

    # 2) Keyword-based classification
    scores: dict[str, int] = {}
    for etype, keywords in cfg.get("expense_types", {}).items():
        score = 0
        for kw in keywords:
            kw_lc = kw.lower()
            if kw_lc and (kw_lc in text_lc or kw_lc in name_lc):
                score += 1
        if score > 0:
            scores[etype] = score

    if scores:
        # Highest score wins
        return max(scores.items(), key=lambda kv: kv[1])[0]

    return None


def sanitize_vendor(vendor: str) -> str:
    # Sanitize vendor while keeping meaningful casing (e.g. APCOA, UBER, GWR).
    vendor = vendor.strip()
    if not vendor:
        return "Unknown"
    vendor = re.sub(r"[^\w.-]+", "_", vendor)
    vendor = re.sub(r"_+", "_", vendor).strip("_")
    return vendor or "Unknown"


def extract_vendor_from_text(text: str) -> str | None:
    # Look for common labels
    for line in text.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue
        lower = line_stripped.lower()
        if any(
            k in lower
            for k in ["merchant", "vendor", "store", "shop", "restaurant", "hotel", "company"]
        ):
            # Take the whole line
            return line_stripped
    # Fallback: first non-empty line
    for line in text.splitlines():
        line_stripped = line.strip()
        if line_stripped:
            return line_stripped
    return None


def extract_vendor_from_filename(name: str) -> str | None:
    base = name.rsplit(".", 1)[0]
    # Remove obvious date-like patterns and digits/underscores
    # Replace separators with spaces
    tmp = re.sub(r"[._-]+", " ", base)
    tmp = re.sub(r"\d{1,4}", " ", tmp)
    tmp = re.sub(r"\s+", " ", tmp).strip()
    if not tmp:
        return None
    # Drop generic trailing words like 'receipt' or 'invoice'
    tmp = re.sub(r"\b(receipt|invoice|facture|receipt no.*)$", "", tmp, flags=re.IGNORECASE).strip()
    return tmp or None


def get_candidate_dates(text: str, filename: str, is_image: bool) -> tuple[dt.date | None, dt.date | None]:
    """Return (content_date, filename_date) candidates for a file.

    content_date is chosen from all plausible content dates, preferring the one
    closest to the filename date when available; otherwise the earliest.

    For image files where OCR is very noisy and no month name is present in the
    text, we treat numeric content dates as unreliable and rely on the filename
    date instead.
    """
    content_dates = extract_dates_from_text(text)
    text_lc = text.lower()
    has_month_word = any(
        m in text_lc for m in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )

    # Use the first content date's year as a hint for filename parsing; fall back to today.
    fallback_year = content_dates[0].year if content_dates else dt.date.today().year
    filename_date = extract_date_from_filename(filename, fallback_year=fallback_year)

    # Detect camera-style filenames like 20251029_081939 or 20251231_215342.
    camera_style = bool(re.search(r"\b\d{8}_\d{6}\b", filename))

    if is_image and camera_style and filename_date is not None and content_dates:
        # If OCR date disagrees with camera timestamp, trust the filename date and
        # mark it as uncertain (the caller will set from_filename_only = True).
        if filename_date not in content_dates:
            content_dates = []
    elif is_image and content_dates and filename_date is not None and not has_month_word:
        # Likely an orange ticket / noisy OCR scenario: ignore numeric content dates.
        content_dates = []

    content_date: dt.date | None = None
    if content_dates:
        if filename_date is not None:
            # Choose the content date closest to the filename date.
            content_date = min(content_dates, key=lambda d: abs((d - filename_date).days))
        else:
            # No filename hint: use earliest content date.
            content_date = content_dates[0]

    return content_date, filename_date


def ask_user_for_date(path: Path, content_date: dt.date, filename_date: dt.date) -> dt.date | None:
    """Ask the user which date to use when content and filename disagree massively.

    Returns the chosen date or None to indicate "no decision".
    """
    print(f"\nAMBIGUOUS DATES for {path}:")
    print(f"  1) From content : {content_date.isoformat()}")
    print(f"  2) From filename: {filename_date.isoformat()}")
    choice = input("Choose date [1=content, 2=filename, empty=skip]: ").strip()
    if choice == "1":
        return content_date
    if choice == "2":
        return filename_date
    return None


def get_date_for_file(path: Path, text: str, filename: str, dry_run: bool) -> tuple[dt.date | None, bool]:
    """Determine the most reliable date for the receipt.

    Policy:
    - Prefer date from *content* when available (within the allowed year window).
    - Use filename date only when no plausible content date exists.
    - If both exist and differ by a large margin (>180 days), ask the user
      (when not in dry-run mode); otherwise, use the content date.

    Returns (date, from_filename_only).
    """
    is_image = path.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".heic"}
    content_date, filename_date = get_candidate_dates(text, filename, is_image)

    if content_date and not filename_date:
        return content_date, False
    if filename_date and not content_date:
        return filename_date, True
    if not content_date and not filename_date:
        return None, False

    # Both dates exist. Check if they are reasonably close.
    delta_days = abs((content_date - filename_date).days)
    if delta_days <= 180:
        # Smallish difference: trust content.
        return content_date, False

    # Large difference; try to be clever and ask when allowed.
    if not dry_run:
        chosen = ask_user_for_date(path, content_date, filename_date)
        if chosen is not None:
            return chosen, False

    # In dry-run or if user skipped, default to content but warn.
    print(
        f"[WARN] Ambiguous dates for {path}: content={content_date.isoformat()}, "
        f"filename={filename_date.isoformat()}, delta={delta_days} days. Using content date."
    )
    return content_date, False


def get_time_for_file(text: str) -> dt.time | None:
    return extract_time_from_text(text)


def ask_user_for_type_and_provider(path: Path, cfg: dict) -> tuple[str | None, str | None]:
    """Interactively ask the user for expense type and optional provider.

    Returns (expense_type, provider_override), where provider_override is a
    UPPERCASED provider name that should be used as the vendor for *this* file
    in addition to being learned for future detection.
    """
    print(f"\nCould not determine expense type for: {path}")
    existing = sorted(cfg.get("expense_types", {}).keys())
    if existing:
        print("Known types:", ", ".join(existing))
    user_type = input("Enter expense type (existing or new, empty to skip): ").strip()
    if not user_type:
        return None, None
    user_type = user_type.lower()

    if user_type not in cfg.get("expense_types", {}):
        print(f"New expense type '{user_type}'.")
        kw_input = input("Enter comma-separated keywords for this type (optional): ").strip()
        if kw_input:
            keywords = [k.strip() for k in kw_input.split(",") if k.strip()]
        else:
            keywords = []
        cfg.setdefault("expense_types", {})[user_type] = keywords

    provider_override: str | None = None
    prov_input = input(
        "Enter a provider keyword for this receipt (e.g. uber, apcoa, gwr) to map to this type (optional): "
    ).strip()
    if prov_input:
        prov_key = prov_input.lower()
        cfg.setdefault("providers", {})[prov_key] = user_type
        provider_override = prov_key.upper()

    return user_type, provider_override


def format_date_for_name(d: dt.date | None, from_filename_only: bool) -> str:
    if d is None:
        return "00.00.0000"
    base = f"{d.day:02d}.{d.month:02d}.{d.year:04d}"
    if from_filename_only:
        return base + "[]"
    return base


def build_new_name(
    date: dt.date | None, from_filename_only: bool, etype: str | None, vendor: str | None, ext: str
) -> str:
    date_part = format_date_for_name(date, from_filename_only)
    raw_type = (etype or "unknown").strip().lower()
    # Capitalize expense type for readability (Train, Parking, Taxi, ...)
    type_part = raw_type.capitalize()
    vendor_part = sanitize_vendor(vendor or "unknown")
    return f"{date_part}-{type_part}-{vendor_part}{ext.lower()}"


def unique_target_path(target_dir: Path, name: str) -> Path:
    base, ext = name.rsplit(".", 1) if "." in name else (name, "")
    candidate = target_dir / name
    counter = 1
    while candidate.exists():
        suffix = f"-{counter}"
        new_name = f"{base}{suffix}.{ext}" if ext else f"{base}{suffix}"
        candidate = target_dir / new_name
        counter += 1
    return candidate


def is_vendor_noise(vendor: str) -> bool:
    # Heuristically detect obviously bogus vendor strings from noisy OCR.
    if not vendor:
        return True
    core = vendor.replace("_", "")
    if len(core) < 3:
        return True
    if len(core) > 40:
        return True
    letters = sum(1 for c in core if c.isalpha())
    if letters == 0:
        return True
    ratio = letters / len(core)
    # Require at least ~40% letters to consider it a plausible name.
    if ratio < 0.4:
        return True
    # Require at least one run of 3+ letters.
    if not re.search(r"[A-Za-z]{3,}", core):
        return True
    return False


def process_file(path: Path, base_dir: Path, cfg: dict, dry_run: bool) -> None:
    global STATS
    if path.is_dir():
        return

    if is_under_originals(base_dir, path):
        STATS["skipped_originals"] += 1
        return

    # Skip config and this script itself
    if path.name.lower() in {"expense_config.json", "rename_receipts.py"}:
        STATS["skipped_internal"] += 1
        return

    # Skip non-receipt file types (e.g. helper scripts)
    allowed_exts = {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".tif",
        ".tiff",
        ".bmp",
        ".gif",
        ".heic",
        ".txt",
        ".log",
        ".csv",
        ".htm",
        ".html",
    }
    if path.suffix.lower() not in allowed_exts:
        STATS["skipped_internal"] += 1
        return

    # If the file already follows our target naming scheme, don't touch it.
    if already_in_target_format(path):
        STATS["skipped_already_named"] += 1
        return

    STATS["scanned"] += 1

    text = extract_text_for_file(path)
    filename = path.name

    date, from_filename_only = get_date_for_file(path, text, filename, dry_run)
    tx_time = get_time_for_file(text)
    etype = infer_expense_type(text, filename, tx_time, cfg)

    provider_override: str | None = None
    if etype is None:
        if dry_run:
            etype = cfg.get("fallback_type", "other")
        else:
            etype, provider_override = ask_user_for_type_and_provider(path, cfg)
            etype = etype or cfg.get("fallback_type", "other")

    text_lc = text.lower()
    name_lc = filename.lower()
    provider_key = detect_provider(text_lc, name_lc, cfg)

    is_image = path.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".heic"}

    if provider_override is not None:
        vendor_raw = provider_override
    elif provider_key is not None:
        # Use provider keyword (uppercased) as the vendor label, e.g. APCOA, UBER, GWR.
        vendor_raw = provider_key.upper()
    else:
        if is_image:
            # For images without a known provider, do not trust OCR for vendor.
            vendor_raw = "Unknown"
        else:
            vendor_raw = (
                extract_vendor_from_text(text)
                or extract_vendor_from_filename(filename)
                or "Unknown"
            )

    if is_vendor_noise(vendor_raw):
        vendor_raw = "Unknown"

    vendor = vendor_raw
    new_name = build_new_name(date, from_filename_only, etype, vendor, path.suffix)
    target = unique_target_path(path.parent, new_name)

    rel_old = path.relative_to(base_dir)
    rel_new = target.relative_to(base_dir)

    if dry_run:
        print(f"[DRY-RUN] {rel_old} -> {rel_new}")
        STATS["renamed"] += 1
    else:
        backup_file(base_dir, path)
        print(f"Renaming: {rel_old} -> {rel_new}")
        path.rename(target)
        STATS["renamed"] += 1


def walk_and_process(base_dir: Path, cfg: dict, dry_run: bool) -> None:
    for p in base_dir.rglob("*"):
        if p.is_file():
            process_file(p, base_dir, cfg, dry_run)


def print_header(base_dir: Path, dry_run: bool) -> None:
    mode = "DRY-RUN (no changes will be made)" if dry_run else "LIVE RUN"
    print(f"=== rename_receipts v{VERSION} ===")
    print("Intelligently rename expense receipts based on content and filenames.")
    print(f"Base directory: {base_dir}")
    print(f"Mode: {mode}")
    print()


def print_summary() -> None:
    print("--- Summary ---")
    print(f"Files considered     : {STATS['scanned']}")
    print(f"Renamed (or would be): {STATS['renamed']}")
    print(f"Skipped originals     : {STATS['skipped_originals']}")
    print(f"Skipped internal     : {STATS['skipped_internal']}")
    print(f"Skipped already named: {STATS['skipped_already_named']}")


def main():
    parser = argparse.ArgumentParser(
        description="Intelligently rename receipt files based on content and filename."
    )
    parser.add_argument(
        "--base-dir",
        type=str,
        default=".",
        help="Base directory to scan (default: current directory).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be renamed without changing anything.",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    cfg = load_config(base_dir)

    print_header(base_dir, args.dry_run)
    walk_and_process(base_dir, cfg, args.dry_run)
    print_summary()

    # Save any updates to config (e.g. new types added interactively)
    save_config(base_dir, cfg)


if __name__ == "__main__":
    main()
