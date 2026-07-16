#!/usr/bin/env python3
"""Build the Gemini OCR work queue.

Two selection modes, chosen per source:

- cfg.FULL_CRAWL_SOURCES (zcks, the three telegram crawls, pertokenbadini):
  every PDF under that source's directory is queued directly - no
  needs_ocr split (2026-07-16 decision, see docs/DOCUMENT_AI_OCR_GUIDE.md).
- Everything else (facebook, spirez, sh2_unicodefixed_bahdini): reads
  extractions/<source>/_manifest.jsonl and keeps only the documents the
  native extraction pipeline flagged status="needs_ocr", resolved back to
  their PDF files via the source->directory mapping extract_pipeline.py uses.

Either way, rows are written to gemini_ocr_pipeline/output/manifest.jsonl,
one row per still-present document, with the same doc_id hash run_ocr.py
and run_ocr_openrouter.py both use - so this manifest is consumable by
either backend and reruns resume against whichever backend already OCR'd
a given page.

Missing files (for example sources whose download is still incomplete) are
counted and reported, not treated as errors. Re-run this script whenever new
PDFs finish downloading or the extraction pipeline is re-run.

Run inside the conda "ai" env:
    conda run --no-capture-output -n ai python gemini_ocr_pipeline/build_manifest.py
"""

import argparse
import json
import sys
from collections import Counter

import ocr_config as cfg

sys.path.insert(0, str(cfg.ROOT / "scripts"))
from extract_pipeline import SOURCES  # noqa: E402  (needs sys.path above)

ALL_SOURCES = sorted(set(SOURCES) | set(cfg.FULL_CRAWL_SOURCES))


def full_crawl_rows(source: str, missing: Counter) -> list:
    root = cfg.FULL_CRAWL_SOURCES[source]
    if not root.is_dir():
        missing[source] += 0
        return []
    rows = []
    for path in sorted(root.rglob("*.pdf")):
        input_rel = path.relative_to(root).as_posix()
        rows.append({
            "source": source,
            "input": input_rel,
            "path": str(path.relative_to(cfg.ROOT)),
            "doc_id": cfg.doc_id(source, input_rel),
            "pages": None,
            "bytes": path.stat().st_size,
        })
    return rows


def needs_ocr_rows(source: str, missing: Counter) -> list:
    manifest = cfg.ROOT / "extractions" / source / "_manifest.jsonl"
    if not manifest.is_file():
        return []
    input_dir = SOURCES[source][0]
    rows = []
    with open(manifest, encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("status") != "needs_ocr":
                continue
            # flat (non-recursive) sources predate the "input" field; for
            # them the basename in "file" is the path within the source dir
            input_rel = record.get("input") or record["file"]
            pdf_path = input_dir / input_rel
            if not pdf_path.is_file():
                missing[source] += 1
                continue
            rows.append({
                "source": source,
                "input": input_rel,
                "path": str(pdf_path.relative_to(cfg.ROOT)),
                "doc_id": cfg.doc_id(source, input_rel),
                "pages": record.get("pages"),
                "bytes": record.get("bytes"),
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", action="append", choices=ALL_SOURCES,
                        help="limit to one source (repeatable); default: all")
    args = parser.parse_args()

    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    found = Counter()
    missing = Counter()
    for source in args.source or ALL_SOURCES:
        if source in cfg.FULL_CRAWL_SOURCES:
            source_rows = full_crawl_rows(source, missing)
        else:
            source_rows = needs_ocr_rows(source, missing)
        found[source] = len(source_rows)
        rows.extend(source_rows)

    rows.sort(key=lambda row: (row["source"], row["input"]))
    with open(cfg.MANIFEST_PATH, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    total_pages = sum(row["pages"] or 0 for row in rows)
    print(f"Wrote {len(rows)} documents ({total_pages}+ pages, full-crawl sources "
          f"don't pre-count pages) to {cfg.MANIFEST_PATH}")
    for source in sorted(set(found) | set(missing)):
        mode = "full-crawl" if source in cfg.FULL_CRAWL_SOURCES else "needs_ocr"
        print(f"  {source} ({mode}): {found[source]} queued, {missing[source]} missing on disk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
