import os
import shutil
import subprocess
import multiprocessing
from pathlib import Path
import re

ARABIC_LETTERS = set("ةيكأإؤثذصضطظ")
KURDISH_LETTERS = set("پچژگڤێۆەڕڵیک")

def get_text_from_pdf(pdf_path):
    try:
        # Use pdftotext to extract first 2 pages
        result = subprocess.run(['pdftotext', '-l', '2', '-q', str(pdf_path), '-'],
                                capture_output=True, text=True, timeout=10)
        return result.stdout
    except Exception:
        return ""

def get_text_from_txt(txt_path):
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            return f.read(4000)
    except Exception:
        return ""

def detect_language(text):
    if not text.strip():
        return "unknown"

    text_lower = text.lower()

    # Count chars
    arabic_chars = sum(1 for c in text_lower if '\u0600' <= c <= '\u06FF')
    latin_chars = sum(1 for c in text_lower if 'a' <= c <= 'z')

    if arabic_chars > latin_chars and arabic_chars > 20:
        # It's Arabic script
        ar_score = sum(text_lower.count(c) for c in ARABIC_LETTERS)
        ku_score = sum(text_lower.count(c) for c in KURDISH_LETTERS)

        # Arabic specific words / prefixes
        ar_words = len(re.findall(r'\b(في|من|على|الى|الله|ال)', text_lower))
        ar_score += ar_words * 2

        if ar_score > ku_score:
            return "arabic"
        else:
            return "kurdish_arabic"

    elif latin_chars > arabic_chars and latin_chars > 20:
        # It's Latin script
        ku_latin_chars = sum(text_lower.count(c) for c in ['ê', 'î', 'û', 'ç', 'ş'])
        if ku_latin_chars > 3:
            return "latin_kurdish"
        else:
            return "other_latin"

    return "unknown"

def process_file(file_path):
    ext = file_path.suffix.lower()
    if ext == '.pdf':
        text = get_text_from_pdf(file_path)
    elif ext == '.txt':
        text = get_text_from_txt(file_path)
    else:
        return file_path, "unknown"

    lang = detect_language(text)
    return file_path, lang

def main():
    base_dir = Path("/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler")

    # Folders to search
    target_dirs = ["crawls", "docs", "extractions", "web"]

    # Destination folders
    arabic_dir = base_dir / "crawls" / "arabic_docs"
    latin_kurdish_dir = base_dir / "crawls" / "latin_kurdish_docs"

    arabic_dir.mkdir(parents=True, exist_ok=True)
    latin_kurdish_dir.mkdir(parents=True, exist_ok=True)

    files_to_process = []

    # Gather files
    for d in target_dirs:
        dir_path = base_dir / d
        if not dir_path.exists():
            continue
        for root, dirs, files in os.walk(dir_path):
            # Skip the destination folders themselves
            if arabic_dir.name in root or latin_kurdish_dir.name in root:
                continue
            for file in files:
                if file.lower().endswith(('.pdf', '.txt')):
                    files_to_process.append(Path(root) / file)

    print(f"Total files to process: {len(files_to_process)}")

    arabic_count = 0
    latin_kurdish_count = 0

    with multiprocessing.Pool(processes=multiprocessing.cpu_count()) as pool:
        for file_path, lang in pool.imap_unordered(process_file, files_to_process):
            if lang == "arabic":
                dest = arabic_dir / file_path.name
                # Handle name collisions
                counter = 1
                while dest.exists():
                    dest = arabic_dir / f"{file_path.stem}_{counter}{file_path.suffix}"
                    counter += 1
                try:
                    shutil.move(str(file_path), str(dest))
                    arabic_count += 1
                except Exception as e:
                    pass
            elif lang == "latin_kurdish":
                dest = latin_kurdish_dir / file_path.name
                counter = 1
                while dest.exists():
                    dest = latin_kurdish_dir / f"{file_path.stem}_{counter}{file_path.suffix}"
                    counter += 1
                try:
                    shutil.move(str(file_path), str(dest))
                    latin_kurdish_count += 1
                except Exception as e:
                    pass

    print(f"Arabic docs moved: {arabic_count}")
    print(f"Latin Kurdish docs moved: {latin_kurdish_count}")

if __name__ == '__main__':
    main()
