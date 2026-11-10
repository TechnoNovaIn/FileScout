#!/usr/bin/env python3
import os
import sys
import argparse
import json
import time
import hashlib
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp", ".svg"}
TEXT_EXTENSIONS = {".txt", ".csv", ".tsv", ".log", ".md", ".json", ".py", ".js", ".html", ".css", ".xml", ".sql", ".yaml", ".yml", ".ini", ".conf", ".sh", ".ps1", ".bat", ".cmd", ".rtf", ".env"}
OFFICE_EXTENSIONS = {".docx", ".xlsx"}
SUPPORTED = PDF_EXTENSIONS | IMAGE_EXTENSIONS | TEXT_EXTENSIONS | OFFICE_EXTENSIONS

from extractors.image_extractor import extract_image_text as extract_image_text_wrapper, preprocess_image_for_ocr
from search_engine import find_matches_multilingual
from processors.language_processor import (
    detect_language, resolve_ocr_languages, resolve_tesseract_languages,
    normalize_unicode, has_indic_script, get_script_name,
)
from index_db import IndexDatabase

db = None

def get_db():
    global db
    if db is None:
        db = IndexDatabase()
    return db


def _cache_key(filepath):
    stat = os.stat(filepath)
    raw = f"{filepath}|{stat.st_mtime}|{stat.st_size}"
    return hashlib.md5(raw.encode()).hexdigest()


def extract_docx_text(filepath):
    """Extract full plain text from .docx via standard zipfile XML parser."""
    try:
        with zipfile.ZipFile(filepath) as z:
            xml_content = z.read('word/document.xml')
            tree = ET.fromstring(xml_content)
            texts = []
            for node in tree.iter():
                if node.tag.endswith('}t') and node.text:
                    texts.append(node.text)
                elif node.tag.endswith('}p'):
                    texts.append('\n')
            return "".join(texts).strip(), "docx_native", 1.0
    except Exception:
        return "", None, 0.0


def extract_xlsx_text(filepath):
    """Extract string cells from .xlsx via standard zipfile XML parser."""
    try:
        texts = []
        with zipfile.ZipFile(filepath) as z:
            if 'xl/sharedStrings.xml' in z.namelist():
                tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
                for node in tree.iter():
                    if node.tag.endswith('}t') and node.text:
                        texts.append(node.text)
        return " ".join(texts).strip(), "xlsx_native", 1.0
    except Exception:
        return "", None, 0.0


def extract_pdf_text(filepath, ocr_quality='balanced', ocr_languages=None):
    """Extract text from PDF using hybrid per-page approach.
    Digital text is extracted first; empty or low-density pages undergo in-memory OCR."""
    plumber_pages = []
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            for p in pdf.pages:
                t = p.extract_text() or ""
                plumber_pages.append(t.strip())
    except Exception:
        plumber_pages = []

    # If pdfplumber failed completely, try pypdf
    if not plumber_pages:
        try:
            from pypdf import PdfReader
            reader = PdfReader(filepath)
            for p in reader.pages:
                t = p.extract_text() or ""
                plumber_pages.append(t.strip())
        except Exception:
            plumber_pages = []

    # Check if any page needs OCR (scanned pages or sparse text < 40 chars)
    needs_ocr = False
    if not plumber_pages:
        needs_ocr = True
    else:
        for p_text in plumber_pages:
            if len(p_text) < 40:
                needs_ocr = True
                break

    # If all pages have digital text, return immediately
    if not needs_ocr and plumber_pages:
        return "\n\n".join(plumber_pages), "pdfplumber"

    # Hybrid per-page in-memory rendering and OCR via pypdfium2 + pytesseract
    try:
        import pypdfium2 as pdfium
        import pytesseract

        pdf = pdfium.PdfDocument(filepath)
        total_p = len(pdf)
        text_pages = []
        ocr_lang_str = resolve_tesseract_languages(ocr_languages)
        scale = 2.0 if ocr_quality != 'fast' else 1.5

        for i in range(total_p):
            digital_text = plumber_pages[i] if i < len(plumber_pages) else ""
            # If digital text has sufficient substance, retain it
            if len(digital_text) >= 40:
                text_pages.append(digital_text)
                continue

            # Render page to PIL image in-memory
            page = pdf[i]
            img = page.render(scale=scale).to_pil()
            processed_img = preprocess_image_for_ocr(img)

            try:
                ocr_t = pytesseract.image_to_string(processed_img, lang=ocr_lang_str, config="--psm 3")
            except Exception:
                ocr_t = pytesseract.image_to_string(processed_img, lang="eng", config="--psm 3")

            combined = (digital_text + "\n" + ocr_t).strip()
            text_pages.append(combined)

        return "\n\n".join(text_pages), "hybrid_ocr"
    except Exception:
        pass

    # Fallback to pdftoppm if pypdfium2 is unavailable
    fallback_text = ocr_pdf_fallback(filepath, ocr_quality, ocr_languages)
    if fallback_text.strip():
        return fallback_text, "pdftoppm_ocr"

    if plumber_pages:
        return "\n\n".join(plumber_pages), "pdfplumber"

    return "", None


def ocr_pdf_fallback(filepath, ocr_quality='balanced', ocr_languages=None):
    """Fallback OCR using pdftoppm subprocess."""
    text = ""
    try:
        import subprocess
        import glob
        import pytesseract
        from PIL import Image

        ocr_lang_str = resolve_tesseract_languages(ocr_languages)
        res = 200 if ocr_quality != 'fast' else 150

        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ["pdftoppm", "-jpeg", "-r", str(res), filepath, f"{tmpdir}/page"],
                capture_output=True
            )
            if result.returncode != 0:
                return ""
            for img_path in sorted(glob.glob(f"{tmpdir}/page-*.jpg")):
                try:
                    img = Image.open(img_path)
                    processed_img = preprocess_image_for_ocr(img)
                    try:
                        text += pytesseract.image_to_string(processed_img, lang=ocr_lang_str, config="--psm 3") + "\n"
                    except Exception:
                        text += pytesseract.image_to_string(processed_img, lang="eng", config="--psm 3") + "\n"
                except Exception:
                    pass
    except Exception:
        pass
    return text


def extract_text(filepath, ocr_languages=None, ocr_quality='balanced'):
    if ocr_languages is None:
        ocr_languages = ['en']
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        text, method = extract_pdf_text(filepath, ocr_quality, ocr_languages)
        return text, method, 0.0
    elif ext == ".docx":
        text, method, conf = extract_docx_text(filepath)
        return text, method, conf
    elif ext == ".xlsx":
        text, method, conf = extract_xlsx_text(filepath)
        return text, method, conf
    elif ext in IMAGE_EXTENSIONS:
        text, conf, method = extract_image_text_wrapper(filepath, ocr_languages, ocr_quality)
        return text, method, conf
    elif ext in TEXT_EXTENSIONS:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read(10 * 1024 * 1024)
            return text, "plaintext", 1.0
        except Exception:
            return "", None, 0.0
    return "", None, 0.0


def collect_files(folder):
    """Collect all supported files in a folder, skipping irrelevant directories."""
    folder_path = Path(folder)
    files = []
    for item in folder_path.rglob("*"):
        if not item.is_file():
            continue
        parts = item.parts
        if any(part.startswith('.') or part in ['node_modules', '__pycache__', '.git', 'dist', 'build'] for part in parts):
            continue
        ext = item.suffix.lower()
        if ext in SUPPORTED:
            files.append(item)
    return files


def _index_file(filepath, folder, ocr_languages, ocr_quality):
    """Extract text from a file and store it in SQLite FTS5."""
    rel = str(filepath.relative_to(folder))
    print(f"Indexing: {rel}", file=sys.stderr, flush=True)

    try:
        text, method, confidence = extract_text(str(filepath), ocr_languages, ocr_quality)
    except Exception as e:
        print(f"ERROR: {rel} - extraction failed: {e}", file=sys.stderr, flush=True)
        return rel, False, "unknown", 0.0

    if not text.strip():
        print(f"SKIP: {rel} (no text extracted)", file=sys.stderr, flush=True)
        return rel, False, method or "unknown", confidence

    try:
        text_lang, _ = detect_language(text)
    except Exception:
        text_lang = "en"

    try:
        database = get_db()
        database.add_file(
            filepath=str(filepath),
            filename=rel,
            extension=Path(filepath).suffix.lower(),
            ocr_method=method or "unknown",
            confidence=confidence,
            language=text_lang or "en",
            content_hash=_cache_key(str(filepath)),
            text=text
        )
        print(f"INDEXED: {rel} ({method or 'unknown'}, {confidence:.0%})", file=sys.stderr, flush=True)
        return rel, True, method or "unknown", confidence
    except Exception as e:
        print(f"ERROR: {rel} - database update failed: {e}", file=sys.stderr, flush=True)
        return rel, False, method or "unknown", confidence


def index_folder(folder, ocr_quality='balanced', force=False):
    """Walk a folder, extract text from all supported files, store in FTS5 DB."""
    database = get_db()
    files = collect_files(folder)
    total = len(files)
    print(f"Found {total} supported files in {folder}", file=sys.stderr, flush=True)

    # Prune any files that were deleted or moved from disk
    pruned_count = database.prune_missing_files(folder, {str(f) for f in files})
    if pruned_count > 0:
        print(f"Pruned {pruned_count} deleted/missing files from index.", file=sys.stderr, flush=True)

    # Filter out already-indexed files (unless force is requested)
    uncached = []
    already_indexed = 0
    if not force:
        norm_folder = str(Path(folder).resolve())
        f_slash = norm_folder.rstrip('/\\').replace('\\', '/')
        b_slash = norm_folder.rstrip('/\\').replace('/', '\\')
        cursor = database.conn.cursor()
        cursor.execute(
            "SELECT filepath, content_hash FROM files WHERE (filepath LIKE ? OR filepath LIKE ? OR filepath LIKE ? OR filepath LIKE ?) AND full_text IS NOT NULL",
            (f_slash + '/%', f_slash + '%', b_slash + '\\%', b_slash + '%')
        )
        indexed_map = {row['filepath']: row['content_hash'] for row in cursor.fetchall()}

        for filepath in files:
            ck = _cache_key(str(filepath))
            if indexed_map.get(str(filepath)) == ck:
                already_indexed += 1
                continue
            uncached.append(filepath)
    else:
        uncached = files

    print(f"Already indexed: {already_indexed}, to process: {len(uncached)}", file=sys.stderr, flush=True)

    if not uncached:
        print("All files are up to date.", file=sys.stderr, flush=True)
        return {
            "folder": folder,
            "total_files": total,
            "already_indexed": already_indexed,
            "indexed": 0,
            "skipped": 0,
            "errors": 0,
            "search_time_seconds": 0,
        }

    ocr_languages = resolve_ocr_languages(None)
    folder_obj = Path(folder)
    max_workers = min(os.cpu_count() or 2, 4)
    indexed = 0
    skipped = 0
    errors = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_index_file, fp, folder_obj, ocr_languages, ocr_quality): fp
            for fp in uncached
        }
        for future in as_completed(futures):
            rel, success, method, confidence = future.result()
            if success:
                indexed += 1
            else:
                if method in ("unknown",) and confidence == 0.0:
                    errors += 1
                else:
                    skipped += 1

    elapsed = round(time.time() - start, 2)
    return {
        "folder": folder,
        "total_files": total,
        "already_indexed": already_indexed,
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "search_time_seconds": elapsed,
    }


def index_status(folder):
    """High-speed batch check of folder index status."""
    database = get_db()
    files = collect_files(folder)
    total = len(files)
    if total == 0:
        return {
            "folder": folder,
            "total_files": 0,
            "indexed_files": 0,
            "is_fully_indexed": False,
        }

    norm_folder = str(Path(folder).resolve())
    f_slash = norm_folder.rstrip('/\\').replace('\\', '/')
    b_slash = norm_folder.rstrip('/\\').replace('/', '\\')
    cursor = database.conn.cursor()
    cursor.execute(
        "SELECT filepath, content_hash FROM files WHERE (filepath LIKE ? OR filepath LIKE ? OR filepath LIKE ? OR filepath LIKE ?) AND full_text IS NOT NULL",
        (f_slash + '/%', f_slash + '%', b_slash + '\\%', b_slash + '%')
    )
    indexed_map = {row['filepath']: row['content_hash'] for row in cursor.fetchall()}

    indexed = 0
    for filepath in files:
        ck = _cache_key(str(filepath))
        if indexed_map.get(str(filepath)) == ck:
            indexed += 1

    return {
        "folder": folder,
        "total_files": total,
        "indexed_files": indexed,
        "is_fully_indexed": indexed == total and total > 0,
    }


def search_folder_from_db(folder, keyword, context_chars, search_language=None, client_id=None):
    """Search only from the SQLite FTS5 database."""
    database = get_db()
    if client_id and (not folder or folder in ('', 'all', 'client://', 'client://local', f'client://{client_id}')):
        abs_folder = None
    elif folder and folder.startswith("client://"):
        abs_folder = folder
    elif folder:
        abs_folder = str(Path(folder).resolve())
    else:
        abs_folder = None

    db_results = database.search(keyword, search_language, abs_folder, context_chars=context_chars, client_id=client_id)

    results = []
    for db_result in db_results:
        matches = db_result.get('matches', [])
        results.append({
            "file": db_result['filename'],
            "filepath": db_result['filepath'],
            "match_count": len(matches),
            "matches": matches,
            "extraction_method": db_result['ocr_method'],
            "confidence": db_result['confidence'],
            "detected_language": db_result['language'],
        })
        print(f"DB MATCH: {len(matches)} match(es) in {db_result['filename']}", file=sys.stderr, flush=True)

    return results


def _process_file(filepath, folder, ocr_languages, ocr_quality, keyword_norm, context_chars, search_language):
    rel = str(filepath.relative_to(folder))
    print(f"Scanning: {rel}", file=sys.stderr, flush=True)

    try:
        text, method, confidence = extract_text(str(filepath), ocr_languages, ocr_quality)
    except Exception as e:
        print(f"ERROR: {rel} - extraction failed: {e}", file=sys.stderr, flush=True)
        return None, "", None, 0.0

    if not text.strip():
        print(f"SKIP: {rel} (no text extracted)", file=sys.stderr, flush=True)
        return None, text, method, confidence

    if search_language:
        try:
            text_lang, _ = detect_language(text)
        except Exception:
            text_lang = None
    else:
        text_lang = None

    try:
        matches = find_matches_multilingual(text, keyword_norm, context_chars, search_language)
    except Exception as e:
        print(f"ERROR: {rel} - search failed: {e}", file=sys.stderr, flush=True)
        return None, text, method, confidence

    if matches:
        result = {
            "file": rel,
            "filepath": str(filepath),
            "match_count": len(matches),
            "matches": matches,
            "extraction_method": method or "unknown",
            "confidence": round(confidence, 3),
            "detected_language": text_lang or "en",
        }
        print(f"FOUND: {len(matches)} match(es) in {rel}", file=sys.stderr, flush=True)

        try:
            database = get_db()
            database.add_file(
                filepath=str(filepath),
                filename=rel,
                extension=Path(filepath).suffix.lower(),
                ocr_method=method or "unknown",
                confidence=confidence,
                language=text_lang or "en",
                content_hash=_cache_key(str(filepath)),
                text=text
            )
        except Exception as e:
            print(f"ERROR: {rel} - database update failed: {e}", file=sys.stderr, flush=True)

        return result, text, method, confidence
    else:
        print(f"NONE: {rel}", file=sys.stderr, flush=True)
        return None, text, method, confidence


def search_folder(folder, keyword, context_chars, search_language=None, ocr_quality='balanced', client_id=None):
    folder_path = Path(folder)
    files = collect_files(folder)

    results = []

    ocr_languages = resolve_ocr_languages(search_language)
    keyword_norm = normalize_unicode(keyword)

    supported = []
    for filepath in files:
        ext = filepath.suffix.lower()
        if ext in SUPPORTED:
            supported.append(filepath)

    # First, check database for instant results
    database = get_db()
    abs_folder = str(Path(folder).resolve())
    db_results = database.search(keyword, search_language, abs_folder, context_chars=context_chars, client_id=client_id)

    for db_result in db_results:
        results.append({
            "file": db_result['filename'],
            "filepath": db_result['filepath'],
            "match_count": len(db_result['matches']),
            "matches": db_result['matches'],
            "extraction_method": db_result['ocr_method'],
            "confidence": db_result['confidence'],
            "detected_language": db_result['language'],
        })
        print(f"DATABASE FOUND: {len(db_result['matches'])} match(es) in {db_result['filename']}", file=sys.stderr, flush=True)

    # Check which files are truly unindexed or modified on disk
    f_slash = abs_folder.rstrip('/\\').replace('\\', '/')
    b_slash = abs_folder.rstrip('/\\').replace('/', '\\')
    cursor = database.conn.cursor()
    cursor.execute(
        "SELECT filepath, content_hash FROM files WHERE (filepath LIKE ? OR filepath LIKE ? OR filepath LIKE ? OR filepath LIKE ?) AND full_text IS NOT NULL",
        (f_slash + '/%', f_slash + '%', b_slash + '\\%', b_slash + '%')
    )
    indexed_map = {row['filepath']: row['content_hash'] for row in cursor.fetchall()}

    uncached = []
    for filepath in supported:
        ck = _cache_key(str(filepath))
        if indexed_map.get(str(filepath)) != ck:
            uncached.append(filepath)

    if not uncached:
        return results

    folder_obj = Path(folder)
    max_workers = min(os.cpu_count() or 2, 4)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_file, fp, folder_obj, ocr_languages, ocr_quality,
                keyword_norm, context_chars, search_language
            ): fp
            for fp in uncached
        }
        for future in as_completed(futures):
            fp = futures[future]
            result, text, method, confidence = future.result()
            if result:
                results.append(result)

    return results


def main():
    parser = argparse.ArgumentParser(description="FileScout search engine")
    parser.add_argument("--folder",  "-f", required=False)
    parser.add_argument("--keyword", "-k", required=False)
    parser.add_argument("--context", "-c", type=int, default=80)
    parser.add_argument("--json",    action="store_true", help="Output JSON (default mode)")
    parser.add_argument("--language", "-l", default=None, help="Search language code (en, hi, kn, te, ta, mr)")
    parser.add_argument("--ocr-quality", choices=["fast", "balanced", "best"], default="balanced")
    parser.add_argument("--init-db", action="store_true", help="Initialize database and exit")
    parser.add_argument("--clear-db", action="store_true", help="Clear database and exit")
    parser.add_argument("--index-only", action="store_true", help="Index folder without searching")
    parser.add_argument("--force", action="store_true", help="Force re-indexing all files")
    parser.add_argument("--search-only", action="store_true", help="Search from DB only")
    parser.add_argument("--index-status", action="store_true", help="Check index status for a folder")
    args = parser.parse_args()

    if args.init_db or args.clear_db:
        if args.folder or args.keyword:
            print("Error: --init-db and --clear-db cannot be used with --folder or --keyword", file=sys.stderr)
            sys.exit(1)

    database = get_db()

    if args.init_db:
        print("Database initialized successfully", file=sys.stderr)
        sys.exit(0)

    if args.clear_db:
        database.clear_index()
        print("Database cleared successfully", file=sys.stderr)
        sys.exit(0)

    if not os.path.isdir(args.folder):
        print(json.dumps({"error": f"Not a directory: {args.folder}", "results": []}), flush=True)
        sys.exit(1)

    if args.index_status:
        status = index_status(args.folder)
        print("###JSON_START###")
        print(json.dumps(status))
        print("###JSON_END###", flush=True)
        return

    if args.index_only:
        start = time.time()
        result = index_folder(args.folder, args.ocr_quality, force=args.force)
        elapsed = round(time.time() - start, 2)
        result["search_time_seconds"] = elapsed
        print("###JSON_START###")
        print(json.dumps(result))
        print("###JSON_END###", flush=True)
        return

    if args.search_only:
        start = time.time()
        results = search_folder_from_db(args.folder, args.keyword, args.context, args.language)
        elapsed = round(time.time() - start, 2)
        output = {
            "keyword": args.keyword,
            "folder": args.folder,
            "language": args.language or "en",
            "total_files_matched": len(results),
            "search_time_seconds": elapsed,
            "results": results,
        }
        print("###JSON_START###")
        print(json.dumps(output))
        print("###JSON_END###", flush=True)
        return

    start = time.time()
    results = search_folder(args.folder, args.keyword, args.context, args.language, args.ocr_quality)
    elapsed = round(time.time() - start, 2)

    output = {
        "keyword": args.keyword,
        "folder": args.folder,
        "language": args.language or "en",
        "total_files_matched": len(results),
        "search_time_seconds": elapsed,
        "results": results,
    }
    print("###JSON_START###")
    print(json.dumps(output))
    print("###JSON_END###", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"error": f"Unhandled exception: {e}", "results": []}), flush=True)
        sys.exit(1)
    finally:
        if db:
            db.close()
