#!/usr/bin/env python3
"""Extraction pipeline: harvested PDFs (and raw .txt) -> normalized Bahdini text.

For every source folder the pipeline:
  1. extracts embedded text per page with PyMuPDF (no OCR),
  2. cleans it (NFKC to fold Arabic presentation forms, then KLPT normalize,
     which maps Arabic-only letter variants ي/ك/ة to Kurdish ی/ک/ە),
  3. writes one .txt per input document under extractions/<source>/,
  4. records per-document stats in extractions/<source>/_manifest.jsonl.

Documents whose PDFs carry no usable text layer (scanned books) are flagged
status="needs_ocr" and listed in extractions/needs_ocr.csv so they can later
be routed to Google Cloud Document AI. Nothing is written to extractions/
for them except the manifest row.

Run inside the conda "ai" env:
    conda run --no-capture-output -n ai python -u scripts/extract_pipeline.py
Re-runs are incremental: documents already present in a manifest are skipped
(use --force to redo everything).
"""

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = ROOT / "extractions"

# source name -> (input dir, kind)
SOURCES = {
    "telegram_badini_book": (ROOT / "telegram/downloads/Badini_book", "pdf"),
    "telegram_jihana_pertuken_pdf": (ROOT / "telegram/downloads/jihana_pertuken_pdf", "pdf"),
    "telegram_pertok_badini": (ROOT / "telegram/downloads/pertok_badini", "pdf"),
    "sh2_unicodefixed_bahdini": (ROOT / "Sh2_UnicodeFixed_Bahdini", "txt"),
}

# below this many extracted chars per page the text layer is junk -> OCR
MIN_CHARS_PER_PAGE = 40
# Arabic presentation-forms blocks; a high share of these after extraction
# usually means the PDF stores shaped glyphs and may be in visual order
PRESENTATION_RE = re.compile(r"[ﭐ-﷿ﹰ-﻿]")
ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
# letters that exist in Kurdish Arabic script but not in Arabic itself
KURDISH_RE = re.compile(r"[ڤڵڕێۆپچگژە]")

_preprocessor = None


def get_preprocessor():
    global _preprocessor
    if _preprocessor is None:
        from klpt.preprocess import Preprocess
        _preprocessor = Preprocess("Sorani", "Arabic")  # same alphabet as Bahdini
    return _preprocessor


def clean_text(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw)
    prep = get_preprocessor()
    # normalize() line by line: KLPT strips newlines when fed whole documents
    lines = [prep.normalize(line) if line.strip() else "" for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def text_stats(text: str) -> dict:
    arabic = len(ARABIC_RE.findall(text))
    total_alpha = sum(ch.isalpha() for ch in text)
    return {
        "chars": len(text),
        "arabic_script_ratio": round(arabic / total_alpha, 3) if total_alpha else 0.0,
        "kurdish_chars": len(KURDISH_RE.findall(text)),
    }


def extract_pdf(src: str, pdf_path: str, out_path: str) -> dict:
    import fitz

    rec = {"source": src, "file": os.path.basename(pdf_path), "kind": "pdf"}
    try:
        rec["bytes"] = os.path.getsize(pdf_path)
        with fitz.open(pdf_path) as doc:
            if doc.needs_pass:
                rec.update(status="error", error="password-protected")
                return rec
            pages = [page.get_text("text") for page in doc]
        rec["pages"] = len(pages)
        raw = "\n\f\n".join(pages)
        n_chars = sum(len(p.strip()) for p in pages)
        rec["chars_per_page"] = round(n_chars / max(len(pages), 1), 1)
        rec["empty_page_ratio"] = round(
            sum(len(p.strip()) < MIN_CHARS_PER_PAGE for p in pages) / max(len(pages), 1), 3)

        if rec["chars_per_page"] < MIN_CHARS_PER_PAGE:
            rec.update(status="needs_ocr", reason="no or near-empty text layer")
            return rec

        pres = len(PRESENTATION_RE.findall(raw))
        arabic = len(ARABIC_RE.findall(raw))
        rec["presentation_form_ratio"] = round(pres / arabic, 3) if arabic else 0.0

        text = clean_text(raw)
        rec.update(text_stats(text))
        Path(out_path).write_text(text, encoding="utf-8")
        rec["out"] = os.path.relpath(out_path, ROOT)
        if rec["presentation_form_ratio"] > 0.2:
            # extracted from shaped glyphs; ordering may be visual not logical
            rec["status"] = "extracted_suspect"
        elif rec["empty_page_ratio"] > 0.5:
            # text layer exists but most pages are empty (mixed scan/text)
            rec["status"] = "extracted_partial"
        else:
            rec["status"] = "extracted"
        return rec
    except Exception as e:  # corrupt/truncated downloads land here
        rec.update(status="error", error=f"{type(e).__name__}: {e}")
        return rec


def extract_txt(src: str, txt_path: str, out_path: str) -> dict:
    rec = {"source": src, "file": os.path.basename(txt_path), "kind": "txt"}
    try:
        rec["bytes"] = os.path.getsize(txt_path)
        raw = Path(txt_path).read_text(encoding="utf-8", errors="replace")
        text = clean_text(raw)
        rec.update(text_stats(text))
        Path(out_path).write_text(text, encoding="utf-8")
        rec["out"] = os.path.relpath(out_path, ROOT)
        rec["status"] = "extracted"
        return rec
    except Exception as e:
        rec.update(status="error", error=f"{type(e).__name__}: {e}")
        return rec


def unique_out_path(out_dir: Path, stem: str, taken: set) -> Path:
    safe = re.sub(r"\s+", " ", stem).strip() or "untitled"
    candidate, n = safe, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f"{safe}__{n}"
    taken.add(candidate.lower())
    return out_dir / f"{candidate}.txt"


def load_done(manifest: Path) -> dict:
    done = {}
    if manifest.exists():
        with open(manifest, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    done[rec["file"]] = rec
    return done


def is_done(rec: dict) -> bool:
    # a manifest row only counts if its output text still exists on disk;
    # manifests travel via git but the .txt outputs are gitignored, so on a
    # fresh clone everything must be re-extracted
    if rec.get("out"):
        return (ROOT / rec["out"]).is_file()
    return rec["status"] in ("needs_ocr", "error")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=SOURCES, action="append",
                    help="limit to one source (repeatable); default: all")
    ap.add_argument("--limit", type=int, help="max new documents per source")
    ap.add_argument("--workers", type=int, default=max(os.cpu_count() // 2, 1))
    ap.add_argument("--force", action="store_true", help="reprocess everything")
    args = ap.parse_args()

    for src in args.source or SOURCES:
        in_dir, kind = SOURCES[src]
        if not in_dir.is_dir():
            print(f"[{src}] missing input dir {in_dir}, skipping", file=sys.stderr)
            continue
        out_dir = OUT_ROOT / src
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = out_dir / "_manifest.jsonl"
        done = {} if args.force else {
            name: rec for name, rec in load_done(manifest).items() if is_done(rec)}

        ext = ".pdf" if kind == "pdf" else ".txt"
        files = sorted(p for p in in_dir.iterdir()
                       if p.suffix.lower() == ext and not p.name.startswith("."))
        todo = [p for p in files if p.name not in done]
        if args.limit:
            todo = todo[:args.limit]
        print(f"[{src}] {len(files)} {ext} files, {len(done)} already done, "
              f"{len(todo)} to process", flush=True)
        if todo:
            taken = {Path(r["out"]).stem.lower() for r in done.values() if r.get("out")}
            jobs = [(str(p), str(unique_out_path(out_dir, p.stem, taken))) for p in todo]
            worker = extract_pdf if kind == "pdf" else extract_txt

            mode = "w" if args.force else "a"
            with open(manifest, mode, encoding="utf-8") as mf, \
                    ProcessPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(worker, src, i, o): i for i, o in jobs}
                for n, fut in enumerate(as_completed(futures), 1):
                    rec = fut.result()
                    mf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    mf.flush()
                    if n % 25 == 0 or n == len(jobs):
                        print(f"[{src}] {n}/{len(jobs)}", flush=True)

        # compact: drop superseded duplicate rows, sort by filename so the
        # manifest diffs cleanly in git regardless of processing order
        recs = load_done(manifest)
        with open(manifest, "w", encoding="utf-8") as mf:
            for name in sorted(recs):
                mf.write(json.dumps(recs[name], ensure_ascii=False) + "\n")

    write_summary()


def write_summary():
    summary, ocr_rows = {}, []
    for src in SOURCES:
        manifest = OUT_ROOT / src / "_manifest.jsonl"
        if not manifest.exists():
            continue
        recs = list(load_done(manifest).values())
        by_status = {}
        for r in recs:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
            if r["status"] == "needs_ocr":
                ocr_rows.append((src, r["file"], r.get("pages", ""), r.get("bytes", "")))
        summary[src] = {
            "documents": len(recs),
            "by_status": by_status,
            "extracted_chars": sum(r.get("chars", 0) for r in recs),
        }
    (OUT_ROOT / "extraction_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with open(OUT_ROOT / "needs_ocr.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "file", "pages", "bytes"])
        w.writerows(sorted(ocr_rows))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
