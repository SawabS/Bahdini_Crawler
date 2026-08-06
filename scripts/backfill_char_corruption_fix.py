#!/usr/bin/env python3
"""One-off backfill: apply the clean_text() cp1252/PUA fix (see
qa_generation/README.md, "A third corruption class") to text already
extracted by an earlier run of extract_pipeline.py, without re-parsing the
original PDFs -- the saved .txt files already carry everything the fix
needs. Rewrites affected .txt files in place, refreshes their manifest
stats (now including midword_pua_count), then re-runs classify_source() so
any document newly flagged with mid-word PUA moves from safe/ to
ocr_needed/ using the exact same routing extract_pipeline.py already uses
for other corruption signals.

Local-only: no PDF re-parsing, no network/API calls. Safe to re-run --
recover_cp1252_controls/handle_pua_chars are idempotent on already-clean
text.

    conda run --no-capture-output -n ai python -u scripts/backfill_char_corruption_fix.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_pipeline as ep


def backfill_source(src: str) -> dict:
    manifest = ep.OUT_ROOT / src / "_manifest.jsonl"
    if not manifest.exists():
        return {}

    records = ep.load_done(manifest)
    stats = {"docs_touched": 0, "docs_changed": 0, "docs_now_midword_pua": 0}

    for key, rec in records.items():
        out = rec.get("out")
        if not out or rec.get("status") != "extracted":
            continue
        text_path = ep.ROOT / out
        if not text_path.is_file():
            continue

        stats["docs_touched"] += 1
        before = text_path.read_text(encoding="utf-8")
        after = ep.clean_text(before)
        new_stats = ep.text_stats(after)

        if after != before:
            stats["docs_changed"] += 1
            text_path.write_text(after, encoding="utf-8")

        rec.update(new_stats)
        if new_stats["midword_pua_count"] > 0:
            stats["docs_now_midword_pua"] += 1

    with open(manifest, "w", encoding="utf-8") as f:
        for key in sorted(records):
            f.write(json.dumps(records[key], ensure_ascii=False) + "\n")

    return stats


def main() -> int:
    total = {"docs_touched": 0, "docs_changed": 0, "docs_now_midword_pua": 0}
    for src in sorted(ep.SOURCES):
        stats = backfill_source(src)
        if stats:
            print(f"[{src}] touched {stats['docs_touched']}, "
                  f"changed {stats['docs_changed']}, "
                  f"now flagged mid-word-PUA {stats['docs_now_midword_pua']}")
            for k in total:
                total[k] += stats[k]

    print(f"\nTotal: touched {total['docs_touched']}, changed {total['docs_changed']}, "
          f"newly flagged mid-word-PUA {total['docs_now_midword_pua']}")

    print("\nRe-classifying (moves any newly-flagged doc from safe/ to ocr_needed/)...")
    for src in sorted(ep.SOURCES):
        ep.classify_source(src)
    ep.write_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
