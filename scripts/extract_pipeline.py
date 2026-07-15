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

# source name -> (input dir, kind, recursive)
SOURCES = {
    "telegram_badini_book": (ROOT / "telegram/downloads/Badini_book", "pdf", False),
    "telegram_jihana_pertuken_pdf": (ROOT / "telegram/downloads/jihana_pertuken_pdf", "pdf", False),
    "telegram_pertok_badini": (ROOT / "telegram/downloads/pertok_badini", "pdf", False),
    "facebook": (ROOT / "facebook/pdfs", "pdf", False),
    "sh2_unicodefixed_bahdini": (ROOT / "sources/sh2_unicodefixed", "txt", False),
}

# Crawls are added as sources automatically so a newly downloaded crawl does
# not require another pipeline edit. Their names match their output folders.
CRAWLS_ROOT = ROOT / "crawls"
if CRAWLS_ROOT.is_dir():
    for crawl_dir in sorted(CRAWLS_ROOT.iterdir()):
        documents_dir = crawl_dir / "documents"
        if crawl_dir.is_dir() and documents_dir.is_dir():
            SOURCES[crawl_dir.name] = (documents_dir, "pdf", True)

# below this many extracted chars per page the text layer is junk -> OCR
MIN_CHARS_PER_PAGE = 40
# Arabic presentation-forms blocks; a high share of these after extraction
# usually means the PDF stores shaped glyphs and may be in visual order
PRESENTATION_RE = re.compile(r"[ﭐ-﷿ﹰ-﻿]")
ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
# letters that exist in Kurdish Arabic script but not in Arabic itself
KURDISH_RE = re.compile(r"[ڤڵڕێۆپچگژە]")

# A clean text layer is not enough for the Bahdini training corpus. Very short
# text, non-Arabic-script text, and long text with virtually no Kurdish letters
# are routed to OCR/review instead of the safe corpus.
MIN_SAFE_CHARS = 200
MIN_ARABIC_SCRIPT_RATIO = 0.5
MIN_KURDISH_RATIO = 0.001

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


def extract_pdf(src: str, pdf_path: str, out_path: str, input_path: str) -> dict:
    import fitz

    rec = {
        "source": src,
        "file": os.path.basename(pdf_path),
        "input": input_path,
        "kind": "pdf",
    }
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


def extract_txt(src: str, txt_path: str, out_path: str, input_path: str) -> dict:
    rec = {
        "source": src,
        "file": os.path.basename(txt_path),
        "input": input_path,
        "kind": "txt",
    }
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


def unique_out_path(out_dir: Path, input_path: Path, taken: set) -> Path:
    safe = re.sub(r"\s+", " ", input_path.with_suffix("").as_posix())
    safe = safe.replace("/", "__").strip() or "untitled"
    candidate, n = safe, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f"{safe}__{n}"
    taken.add(candidate.lower())
    return out_dir / f"{candidate}.txt"


def record_key(rec: dict) -> str:
    return rec.get("input", rec["file"])


def load_done(manifest: Path) -> dict:
    done = {}
    if manifest.exists():
        with open(manifest, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    done[record_key(rec)] = rec
    return done


def is_done(rec: dict) -> bool:
    # a manifest row only counts if its output text still exists on disk;
    # manifests travel via git but the .txt outputs are gitignored, so on a
    # fresh clone everything must be re-extracted
    if rec.get("out"):
        return (ROOT / rec["out"]).is_file()
    return rec["status"] in ("needs_ocr", "error")


def classification(rec: dict) -> tuple[str, str]:
    status = rec["status"]
    if status == "needs_ocr":
        return "ocr_needed", rec.get("reason", "no usable text layer")
    if status == "extracted_suspect":
        return "ocr_needed", "Unicode presentation forms may have visual-order text"
    if status == "extracted_partial":
        return "ocr_needed", "most pages have no usable text layer"
    if status == "error":
        return "ocr_needed", rec.get("error", "PDF could not be extracted")
    if rec.get("chars", 0) < MIN_SAFE_CHARS:
        return "ocr_needed", "too little extracted text to assess safely"
    if rec.get("arabic_script_ratio", 0.0) < MIN_ARABIC_SCRIPT_RATIO:
        return "ocr_needed", "extracted text is not predominantly Arabic script"
    if (rec.get("chars", 0) >= 1000 and
            rec.get("kurdish_chars", 0) / rec["chars"] < MIN_KURDISH_RATIO):
        return "ocr_needed", "text is unlikely to be Kurdish Bahdini"
    return "safe", "clean text layer and plausible Kurdish Bahdini"


def classify_source(src: str) -> None:
    out_dir = OUT_ROOT / src
    manifest = out_dir / "_manifest.jsonl"
    if not manifest.exists():
        return

    records = load_done(manifest)
    buckets = {"safe": [], "ocr_needed": []}
    for rec in records.values():
        bucket, reason = classification(rec)
        rec["classification"] = bucket
        rec["classification_reason"] = reason
        buckets[bucket].append(rec)

        if rec.get("out"):
            current = ROOT / rec["out"]
            destination = out_dir / bucket / current.name
            if current != destination and current.is_file() and not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                current.replace(destination)
            if destination.is_file():
                rec["out"] = os.path.relpath(destination, ROOT)

    for bucket, bucket_records in buckets.items():
        bucket_dir = out_dir / bucket
        bucket_dir.mkdir(parents=True, exist_ok=True)
        with open(bucket_dir / "_manifest.jsonl", "w", encoding="utf-8") as f:
            for rec in sorted(bucket_records, key=record_key):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with open(manifest, "w", encoding="utf-8") as f:
        for rec in sorted(records.values(), key=record_key):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=SOURCES, action="append",
                    help="limit to one source (repeatable); default: all")
    ap.add_argument("--limit", type=int, help="max new documents per source")
    ap.add_argument("--workers", type=int, default=max(os.cpu_count() // 2, 1))
    ap.add_argument("--force", action="store_true", help="reprocess everything")
    ap.add_argument("--classify-only", action="store_true",
                    help="sort existing manifest outputs into safe/ and ocr_needed/")
    args = ap.parse_args()

    if args.classify_only:
        for src in args.source or SOURCES:
            classify_source(src)
        write_summary()
        return

    for src in args.source or SOURCES:
        in_dir, kind, recursive = SOURCES[src]
        if not in_dir.is_dir():
            print(f"[{src}] missing input dir {in_dir}, skipping", file=sys.stderr)
            continue
        out_dir = OUT_ROOT / src
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = out_dir / "_manifest.jsonl"
        done = {} if args.force else {
            name: rec for name, rec in load_done(manifest).items() if is_done(rec)}

        ext = ".pdf" if kind == "pdf" else ".txt"
        paths = in_dir.rglob("*") if recursive else in_dir.iterdir()
        files = sorted(p for p in paths
                       if p.suffix.lower() == ext and not p.name.startswith("."))
        todo = [p for p in files if p.relative_to(in_dir).as_posix() not in done]
        if args.limit:
            todo = todo[:args.limit]
        print(f"[{src}] {len(files)} {ext} files, {len(done)} already done, "
              f"{len(todo)} to process", flush=True)
        if todo:
            taken = {Path(r["out"]).stem.lower() for r in done.values() if r.get("out")}
            jobs = [
                (
                    str(p),
                    str(unique_out_path(out_dir, p.relative_to(in_dir), taken)),
                    p.relative_to(in_dir).as_posix(),
                )
                for p in todo
            ]
            worker = extract_pdf if kind == "pdf" else extract_txt

            mode = "w" if args.force else "a"
            with open(manifest, mode, encoding="utf-8") as mf, \
                    ProcessPoolExecutor(max_workers=args.workers) as pool:
                futures = {
                    pool.submit(worker, src, input_file, output_file, input_path): input_file
                    for input_file, output_file, input_path in jobs
                }
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
        classify_source(src)

    write_summary()


def write_summary():
    summary, ocr_rows = {}, []
    for src in SOURCES:
        manifest = OUT_ROOT / src / "_manifest.jsonl"
        if not manifest.exists():
            continue
        recs = list(load_done(manifest).values())
        by_status, by_classification = {}, {}
        for r in recs:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
            bucket, _ = classification(r)
            by_classification[bucket] = by_classification.get(bucket, 0) + 1
            if r["status"] == "needs_ocr":
                ocr_rows.append((src, r["file"], r.get("pages", ""), r.get("bytes", "")))
        summary[src] = {
            "documents": len(recs),
            "by_status": by_status,
            "by_classification": by_classification,
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
