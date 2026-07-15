import os
import shutil
import json
import csv
from pathlib import Path
import re

base_dir = Path("/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler")
crawls_dir = base_dir / "crawls"
facebook_dir = base_dir / "crawls" / "facebook"
telegram_dir = base_dir / "crawls" / "telegram"
extractions_dir = base_dir / "extractions"
unmatched_dir = crawls_dir / "unmatched_restored_pdfs"

if not unmatched_dir.exists():
    print("No unmatched directory found.")
    exit(0)

file_map = {}

def add_to_map(fname, target):
    bname = Path(fname).name
    file_map.setdefault(bname, []).append(target)
    norm_space = re.sub(r'\s+', ' ', bname)
    file_map.setdefault(norm_space, []).append(target)

for csv_path in crawls_dir.glob("*/documents.csv"):
    source_dir = csv_path.parent / "documents"
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                fname = row.get('file') or row.get('path')
                if fname:
                    target = source_dir / fname
                    add_to_map(fname, target)
    except Exception:
        pass

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
                    add_to_map(fname, target)
    except Exception:
        pass

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
                        add_to_map(fname, target)
    except Exception:
        pass

def get_original_name(stem):
    m = re.search(r'_(\d+)$', stem)
    if m:
        return stem[:m.start()]
    return stem

restored = 0
not_found = []

for file_path in unmatched_dir.iterdir():
    if not file_path.is_file():
        continue
        
    bname = file_path.name
    stem = file_path.stem
    ext = file_path.suffix
    
    norm_bname = re.sub(r'\s+', ' ', bname)
    
    targets = file_map.get(bname, [])
    if not targets:
        targets = file_map.get(norm_bname, [])
        
    if not targets:
        orig_stem = get_original_name(stem)
        orig_fname = orig_stem + ext
        norm_orig_fname = re.sub(r'\s+', ' ', orig_fname)
        targets = file_map.get(orig_fname, [])
        if not targets:
            targets = file_map.get(norm_orig_fname, [])
        
    moved = False
    for target in targets:
        if not target.exists():
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
    for f in not_found[:10]:
        print(f)
else:
    print("All unmatched files were successfully restored!")
    shutil.rmtree(unmatched_dir)
