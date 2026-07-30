#!/usr/bin/env python3
"""Assemble Gemini page records into the reviewed Bahdini text corpus.

Reads every gemini_ocr_pipeline/output/pages/<source>/<doc_id>.jsonl produced
by run_ocr.py and writes, under gemini_ocr_pipeline/output/corpus/:

  <source>/<document>.txt   one file per document, pages joined with \\n\\f\\n
                            (the same page separator extract_pipeline.py uses)
  corpus.jsonl              one record per document with language statistics,
                            completeness, cost, and classification
  report.md                 human summary: per-source totals plus a
                            review-first list, the documents that most need
                            attention (non-Kurdish classification, heavy
                            [unclear] use, incomplete pages)
  pretrain_candidate.txt    concatenation of complete, Kurdish-classified
                            documents, a candidate pre-training corpus

Text is normalized exactly like the native-extraction corpus (NFKC + KLPT,
which folds Arabic-only letter variants into their Kurdish forms) so both
corpora can be mixed; pass --no-normalize to keep Gemini's raw output.

This corpus has been reviewed and is accepted for use. The classification
fields and report.md's review-first list exist to prioritize attention on
future OCR batches, not to gate this one.

Run inside the conda "ai" env:
    conda run --no-capture-output -n ai python gemini_ocr_pipeline/compile_corpus.py
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ocr_config as cfg

sys.path.insert(0, str(cfg.ROOT / "scripts"))
from extract_pipeline import (  # noqa: E402  (needs sys.path above)
    ARABIC_RE,
    KURDISH_RE,
    MIN_ARABIC_SCRIPT_RATIO,
    clean_text,
)

# documents whose Kurdish-letter share of Arabic-script text is below this are
# most likely Arabic, not Bahdini; same spirit as extract_pipeline's checks
MIN_KURDISH_LETTER_RATIO = 0.02
FENCE_RE = re.compile(r"^```[a-zA-Z]*\n|\n?```$")


def page_text(record: dict, normalize: bool) -> str:
    text = FENCE_RE.sub("", record["text"].strip())
    return clean_text(text).strip() if normalize else text


def classify(stats: dict) -> str:
    if stats["doc_skipped_not_badini"] or (
            stats["pages_not_badini"] > stats["pages_ok"]):
        return "not_badini"
    if stats["chars"] < 200:
        return "low_text"
    if stats["arabic_script_ratio"] < MIN_ARABIC_SCRIPT_RATIO:
        return "not_arabic_script"
    if stats["kurdish_letter_ratio"] < MIN_KURDISH_LETTER_RATIO:
        return "arabic_not_kurdish"
    return "kurdish"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-normalize", action="store_true",
                        help="skip NFKC+KLPT normalization of Gemini output")
    args = parser.parse_args()
    normalize = not args.no_normalize

    record_files = sorted(cfg.PAGES_DIR.glob("*/*.jsonl"))
    if not record_files:
        sys.exit(f"No page records under {cfg.PAGES_DIR}; run run_ocr.py first.")

    documents = []
    for record_file in record_files:
        latest = {}
        with open(record_file, encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                latest[record["page"]] = record
        doc_skipped = latest.pop(0, None) is not None  # page 0 = skip marker
        if not latest:
            continue

        sample = next(iter(latest.values()))
        pages = []
        statuses = Counter()
        cost = 0.0
        for page_number in sorted(latest):
            record = latest[page_number]
            statuses[record["status"]] += 1
            cost += record.get("est_cost_usd", 0.0)
            if record["status"] == "ok":
                pages.append(page_text(record, normalize))

        text = "\n\f\n".join(pages).strip()
        arabic = len(ARABIC_RE.findall(text))
        kurdish = len(KURDISH_RE.findall(text))
        total_alpha = sum(ch.isalpha() for ch in text) or 1
        n_pages = sample["n_pages"]
        resolved = (statuses["ok"] + statuses["no_text"] + statuses["blank"]
                    + statuses["not_badini"])
        stats = {
            "source": sample["source"],
            "file": sample["file"],
            "doc_id": sample["doc_id"],
            "model": sample["model"],
            "prompt_version": sample["prompt_version"],
            "n_pages": n_pages,
            "pages_ok": statuses["ok"],
            "pages_no_text": statuses["no_text"] + statuses["blank"],
            "pages_not_badini": statuses["not_badini"],
            "pages_failed": statuses["error"] + statuses["empty"],
            "doc_skipped_not_badini": doc_skipped,
            "completeness": round(resolved / n_pages, 3),
            "chars": len(text),
            "unclear_marks": text.count(cfg.UNCLEAR_MARKER),
            "arabic_script_ratio": round(arabic / total_alpha, 3),
            "kurdish_letter_ratio": round(kurdish / arabic, 3) if arabic else 0.0,
            "est_cost_usd": round(cost, 4),
            "review_status": "reviewed",
        }
        stats["classification"] = classify(stats)

        if text:
            out_path = cfg.CORPUS_DIR / stats["source"] / (Path(stats["file"]).stem + ".txt")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text + "\n", encoding="utf-8")
            stats["out"] = str(out_path.relative_to(cfg.ROOT))
        documents.append(stats)

    cfg.CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(cfg.CORPUS_DIR / "corpus.jsonl", "w", encoding="utf-8") as handle:
        for stats in documents:
            handle.write(json.dumps(stats, ensure_ascii=False) + "\n")

    pretrain_docs = [
        stats for stats in documents
        if stats["classification"] == "kurdish" and stats["completeness"] == 1.0
    ]
    with open(cfg.CORPUS_DIR / "pretrain_candidate.txt", "w",
              encoding="utf-8") as handle:
        for stats in pretrain_docs:
            handle.write((cfg.ROOT / stats["out"]).read_text(encoding="utf-8"))
            handle.write("\n\n")

    by_source = defaultdict(lambda: Counter())
    for stats in documents:
        counter = by_source[stats["source"]]
        counter["docs"] += 1
        counter["pages_ok"] += stats["pages_ok"]
        counter["chars"] += stats["chars"]
        counter[stats["classification"]] += 1

    review_first = sorted(
        (stats for stats in documents if stats["classification"] != "kurdish"
         or stats["unclear_marks"] > 20 or stats["completeness"] < 1.0),
        key=lambda stats: (stats["classification"] == "kurdish", -stats["unclear_marks"]),
    )
    lines = ["# Gemini OCR corpus report", ""]
    lines.append(f"Documents: {len(documents)}  |  pre-train candidates: "
                 f"{len(pretrain_docs)}  |  total estimated cost: "
                 f"${sum(stats['est_cost_usd'] for stats in documents):.2f}")
    lines += ["", "| source | docs | pages ok | chars | kurdish | other |",
              "|---|---|---|---|---|---|"]
    for source in sorted(by_source):
        counter = by_source[source]
        lines.append(
            f"| {source} | {counter['docs']} | {counter['pages_ok']} | "
            f"{counter['chars']} | {counter['kurdish']} | "
            f"{counter['docs'] - counter['kurdish']} |")
    lines += ["", "## Review first", ""]
    for stats in review_first[:40]:
        lines.append(
            f"- `{stats['source']}/{stats['file']}` - {stats['classification']}, "
            f"completeness {stats['completeness']}, "
            f"{stats['unclear_marks']} [unclear] marks")
    (cfg.CORPUS_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Compiled {len(documents)} documents into {cfg.CORPUS_DIR}")
    print(f"Pre-train candidates: {len(pretrain_docs)}")
    print(f"Report: {cfg.CORPUS_DIR / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
