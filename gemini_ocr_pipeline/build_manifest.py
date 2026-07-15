#!/usr/bin/env python3
"""Build the Gemini OCR work queue from the extraction manifests.

Reads every extractions/<source>/_manifest.jsonl, keeps the documents that the
native extraction pipeline flagged status="needs_ocr", resolves them back to
their PDF files via the same source->directory mapping extract_pipeline.py
uses, and writes one queue row per still-present document to
gemini_ocr_pipeline/output/manifest.jsonl.

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", choices=sorted(SOURCES),
                        help="limit to one source (repeatable); default: all")
    args = parser.parse_args()

    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    found = Counter()
    missing = Counter()
    for source in args.source or sorted(SOURCES):
        manifest = cfg.ROOT / "extractions" / source / "_manifest.jsonl"
        if not manifest.is_file():
            continue
        input_dir = SOURCES[source][0]
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
                found[source] += 1
                rows.append({
                    "source": source,
                    "input": input_rel,
                    "path": str(pdf_path.relative_to(cfg.ROOT)),
                    "doc_id": cfg.doc_id(source, input_rel),
                    "pages": record.get("pages"),
                    "bytes": record.get("bytes"),
                })

    rows.sort(key=lambda row: (row["source"], row["input"]))
    with open(cfg.MANIFEST_PATH, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    total_pages = sum(row["pages"] or 0 for row in rows)
    print(f"Wrote {len(rows)} documents ({total_pages} pages) to {cfg.MANIFEST_PATH}")
    for source in sorted(set(found) | set(missing)):
        print(f"  {source}: {found[source]} queued, {missing[source]} missing on disk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
