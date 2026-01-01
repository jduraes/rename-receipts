# rename_receipts v1.1.0

MIT Licence (C) 2026 Joao Miguel Duraes

`rename_receipts.py` is a Python tool that recursively renames expense receipt files based on their content and existing filenames.
It is designed to clean up directories full of PDFs and images with messy names (mail exports, camera snapshots, etc.) and produce
consistent, human-readable names like:

- `05.11.2025-Parking-RINGGO.pdf`
- `19.11.2025-Train-GWR.jpg`
- `22.09.2025-Dinner-KFC.pdf`

The script has been iterated with real-world data and a lot of edge cases. This repository captures the current state of the script,
its configuration, and the design decisions that evolved while getting it to behave reliably.

## Current capabilities

- **Recursive scan** of a base directory, skipping non-receipt file types.
- **Backup support**: original files are copied under an `originals/` tree on first rename (feature can be disabled or removed if not needed).
- **Intelligent date extraction**:
  - Prefer **content dates** (text in receipts) when they are clearly valid and trustworthy.
  - Extract dates from many formats using `python-dateutil`:
    - `DD/MM/YYYY`, `DD.MM.YYYY`, `YYYY-MM-DD`, textual months like `22 Sep 2025`.
  - When content has no usable date, fall back to **filename dates** using patterns like:
    - `YYYYMMDD` (e.g. `20251105_094006.jpg` → `05.11.2025`).
  - For **camera-style images** with names like `20251029_081939.jpg` or `20251231_215342.jpg`, the `YYYYMMDD` part is treated as
    the canonical date. If OCR finds a different date, the filename date is used and the script marks it as uncertain with `[]`:
    - `29.10.2025[]-Train-Unknown.jpg`.

- **Time extraction and meal classification**:
  - Looks for `HH:MM` times in the receipt text.
  - Uses configurable time buckets to classify meals:
    - Breakfast: 04:00–10:59
    - Lunch: 11:00–16:59
    - Dinner: 17:00–03:59
  - If a receipt looks like a meal (using `meal_keywords`) and has a time, the script assigns `Breakfast`, `Lunch`, or `Dinner` accordingly.

- **Vendor / provider detection**:
  - A `providers` map in `expense_config.json` maps keywords to an expense type and implicitly to a vendor label:
    - `uber` → `Taxi`
    - `apcoa` → `Parking`
    - `ringgo` → `Parking`
    - `gwr`, `great western railway` → `Train`
    - `kfc` → `Other` (so meal classification is still decided by time, not just the brand)
  - Provider keywords are searched in both the OCR text and the filename.
  - When a provider is found, it is used as the vendor label (uppercased) in the filename (e.g. `UBER`, `APCOA`, `RINGGO`, `GWR`, `KFC`).

- **Heuristics for non-provider vendors**:
  - For PDF/TXT/HTML receipts without a known provider, the script tries to extract a vendor from lines containing
    words like `merchant`, `vendor`, `store`, `shop`, `restaurant`, `hotel`, `company`.
  - As a fallback, it derives a vendor from the filename by removing numeric fragments and suffixes like `receipt`, `invoice`.
  - All vendor strings are passed through a noise filter that rejects obvious OCR garbage (too short, no letters, extremely unbalanced).
  - For **images** without a provider, the script does **not** trust OCR vendor text at all and simply uses `Unknown`.

- **Consistent filename format**:
  - `DD.MM.YYYY[-if-uncertain[]]-Type-Vendor.ext`
  - `Type` is capitalized (e.g. `Parking`, `Train`, `Taxi`, `Breakfast`, `Lunch`, `Dinner`, `Other`).
  - `Vendor` is a cleaned, sensible label (e.g. `RINGGO`, `APCOA`, `UBER`, `KFC`, `GWR`, or `Unknown`).
  - When the date comes from a filename because content was missing or conflicting, the script appends `[]` after the date to signal
    that the date might be approximate.

- **Interactive classification**:
  - When the script cannot determine an expense type from content/filename, it prompts the user:
    - Asks for an existing or new expense type.
    - Optionally asks for a provider keyword to map to that type (e.g. `GWR` → `Train`).
  - When a new provider is entered, it is stored in `expense_config.json` and also used immediately as the vendor for that specific file.

- **Safety and idempotence**:
  - Files already in the final naming format are skipped by default.
  - Exact `…-Unknown.ext` vendors are treated as final and never changed.
  - Duplicates (same date/type/vendor) use numeric suffixes `-1`, `-2`, etc. only when a base filename already exists.
  - Non-receipt file types (e.g. `.py` helper scripts) are ignored.

## Design considerations and hurdles

This script has been hardened against a variety of real-world issues:

- **Image-only PDFs**: Many emailed receipts (e.g. KFC app receipts) are image-only PDFs with no text layer. To handle these, the
  script now renders pages with `pdfplumber` and runs Tesseract OCR when `extract_text()` returns nothing.

- **Conflicting dates**: Receipts often contain multiple dates (order date, settlement date, footers with company incorporation dates,
  etc.). The script:
  - Filters dates to a sliding window of `(today.year - 3)` to `(today.year + 1)`.
  - For images with camera-style filenames, it trusts the `YYYYMMDD` timestamp over noisy OCR and uses `[]` to mark that choice.
  - For other receipts, it prefers the earliest plausible date in the content, but will ask the user when content and filename differ
    significantly.

- **OCR noise in vendors**: Raw OCR often produces nonsense vendor strings. The script:
  - Avoids using OCR vendor text for images unless a known provider is detected.
  - Applies a noise filter to any vendor candidate before accepting it.
  - Normalises trailing `"receipt"` / `"invoice"` suffixes out of filename-derived vendors.

|- **Evolving configuration**: `expense_config.json` is designed to grow over time:
  - New providers and types can be added interactively.
  - Meal time buckets and keywords can be tuned without touching the main script.

## Usage

1. Install dependencies:

```powershell
pip install pdfplumber pillow pytesseract python-dateutil
```

Install Tesseract OCR:

- **Windows** – Use a Windows installer (for example, the UB-Mannheim builds). The script will try common locations under
  `C:\\Program Files\\Tesseract-OCR` and `C:\\Program Files (x86)\\Tesseract-OCR`.
- **macOS** – Install via Homebrew:
  `brew install tesseract`
- **Linux** – Install via your distribution's package manager, for example on Debian/Ubuntu:
  `sudo apt-get install tesseract-ocr`

If Tesseract is installed in a non-standard location, you can point the script at it by setting the `TESSERACT_CMD` environment
variable to the full path of the `tesseract` binary before running Python.

2. Place `rename_receipts.py` and `expense_config.json` in a directory on your PATH (e.g. `~/bin`) or run them directly.

3. From a receipts directory you want to clean up:

```powershell
# Dry run first
python path\to\rename_receipts.py --base-dir . --dry-run

# If the output looks good
python path\to\rename_receipts.py --base-dir .
```

4. The script will:

- Print a header with version, base directory, and mode.
- Print each proposed rename (`[DRY-RUN] old -> new` or `Renaming: old -> new`).
- Print a summary of how many files were scanned, renamed, or skipped.

### Command-line parameters

- `--base-dir PATH` – Base directory to scan. Defaults to the current directory (`.`). You can pass either a relative or an absolute path.
- `--dry-run` – Shows what would be renamed without making any changes to files.

## Git repository structure

- `rename_receipts.py` – main script with all logic.
- `expense_config.json` – default configuration (types, providers, meal time buckets [no pun intended], keywords).
- `README.md` – this document.

## Licence

MIT Licence (C) 2026 Joao Miguel Duraes

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
