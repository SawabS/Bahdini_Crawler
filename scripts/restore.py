import os
import shutil
import json
import csv
from pathlib import Path
import re

base_dir = Path("/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler")
crawls_dir = base_dir / "crawls"
facebook_dir = crawls_dir / "facebook"
telegram_dir = crawls_dir / "telegram"
extractions_dir = base_dir / "extractions"

file_map = {} # filename -> list of possible target paths

# 1. Parse crawls/*/documents.csv
for csv_path in crawls_dir.glob("*/documents.csv"):
    source_dir = csv_path.parent / "documents"
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'file' in row:
                    fname = row['file']
                    target = source_dir / fname
                    file_map.setdefault(fname, []).append(target)
    except Exception as e:
        pass

# 2. Parse crawls/facebook/manifest.json
fb_manifest = facebook_dir / "manifest.json"
if fb_manifest.exists():
    fb_pdfs_dir = facebook_dir / "pdfs"
    try:
        with open(fb_manifest, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for k, v in data.items():
                if 'filename' in v:
                    fname = v['filename']
                    target = fb_pdfs_dir / fname
                    file_map.setdefault(fname, []).append(target)
    except Exception:
        pass

# 3. Parse crawls/telegram/downloads/*/.download_state.json
for state_file in telegram_dir.glob("downloads/*/.download_state.json"):
    source_dir = state_file.parent
    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if 'documents' in data:
                for k, v in data['documents'].items():
                    if 'filename' in v:
                        fname = v['filename']
                        target = source_dir / fname
                        file_map.setdefault(fname, []).append(target)
    except Exception:
        pass

# 4. Generate txt paths in extractions
for fname, targets in list(file_map.items()):
    if fname.lower().endswith(".pdf"):
        txt_fname = fname[:-4] + ".txt"
        for target in targets:
            parts = target.parts
            if "facebook" in parts:
                txt_target = extractions_dir / "facebook" / txt_fname
                file_map.setdefault(txt_fname, []).append(txt_target)
            elif "telegram" in parts:
                idx = parts.index("downloads")
                source = ("telegram_" + parts[idx+1]).lower()
                txt_target = extractions_dir / source / txt_fname
                file_map.setdefault(txt_fname, []).append(txt_target)
            elif "crawls" in parts:
                idx = parts.index("crawls")
                source = parts[idx+1]
                txt_target = extractions_dir / source / txt_fname
                file_map.setdefault(txt_fname, []).append(txt_target)

def get_original_name(stem):
    m = re.search(r'_(\d+)$', stem)
    if m:
        return stem[:m.start()]
    return stem

restored = 0
not_found = []

for folder in ["arabic_docs", "latin_kurdish_docs"]:
    src_dir = crawls_dir / folder
    if not src_dir.exists():
        continue

    for file_path in src_dir.iterdir():
        if not file_path.is_file():
            continue

        fname = file_path.name
        stem = file_path.stem
        ext = file_path.suffix

        # Try exact match
        targets = file_map.get(fname, [])
        if not targets:
            # Try stripped name
            orig_stem = get_original_name(stem)
            orig_fname = orig_stem + ext
            targets = file_map.get(orig_fname, [])

        moved = False
        for target in targets:
            if not target.exists():
                # Move here
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file_path), str(target))
                restored += 1
                moved = True
                break

        if not moved:
            not_found.append(file_path.name)

print(f"Restored {restored} files.")
if not_found:
    print(f"Not found: {len(not_found)}")
    print("Samples:", not_found[:10])

# Remove empty directories
for folder in ["arabic_docs", "latin_kurdish_docs"]:
    d = crawls_dir / folder
    if d.exists() and not any(d.iterdir()):
        d.rmdir()
