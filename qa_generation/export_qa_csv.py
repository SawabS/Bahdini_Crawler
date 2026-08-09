#!/usr/bin/env python3
"""Export the generated QA pairs to CSV for human quality review.

This is a *review* artifact, deliberately separate from
compile_qa_dataset.py, which produces the deliverable the partner trains on
(nested messages+metadata JSONL, unreadable in a spreadsheet). Here every QA
pair is one flat row next to the context it was grounded in, so a Bahdini
speaker can read question / answer / source text side by side and judge
dialect purity and grounding without touching JSON.

Two files land in output/dataset/:

  qa_review_all.csv      every generated pair, full context. Large -- it
                         repeats each ~900-token chunk once per pair.
  qa_review_sample.csv   a stratified sample, --per-cell rows drawn evenly
                         from each (source, question_type) cell so no single
                         big source dominates what you end up reading.

Written with a UTF-8 BOM (encoding="utf-8-sig"). Excel assumes the system
legacy codepage for a BOM-less UTF-8 CSV and turns Arabic-script text into
mojibake; the BOM is what makes it open correctly on both Excel and
Numbers. LibreOffice and pandas are fine either way.

Run inside the conda "ai" env, from the repo root:
    python3 qa_generation/export_qa_csv.py
    python3 qa_generation/export_qa_csv.py --per-cell 100 --context-chars 1200
"""

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict

import qa_config as cfg

SAMPLE_SEED = 42
LATIN_RE = re.compile(r"[A-Za-z]")

COLUMNS = [
    "source", "origin", "document_id", "chunk_id", "pair_index",
    "question_type", "question", "answer", "reasoning", "has_reasoning",
    "question_chars", "answer_chars", "latin_chars_in_answer",
    "context", "context_chars", "model", "prompt_version", "ts",
]


def iter_pairs():
    """One dict per generated QA pair, in a single streaming pass over the
    generation records. Context is filled in later (see attach_contexts) --
    resolving it here would mean holding chunks.jsonl in memory at the same
    time as every pair."""
    seen_chunks = set()
    for path in sorted(cfg.GENERATIONS_DIR.glob("*/*.jsonl")):
        source = path.parent.name
        for line in path.open(encoding="utf-8"):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("status") != "ok":
                continue
            chunk_id = record["chunk_id"]
            # A chunk can legitimately appear twice: an interrupted run
            # re-dispatches whatever was in flight when it stopped. Keep the
            # first record only, matching compile_qa_dataset.py.
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            for index, pair in enumerate(record.get("qa_pairs") or []):
                question = pair.get("question") or ""
                answer = pair.get("answer") or ""
                reasoning = pair.get("reasoning") or ""
                yield {
                    "source": source,
                    "origin": chunk_id.split("-", 1)[0],
                    "document_id": record.get("document_id", ""),
                    "chunk_id": chunk_id,
                    "pair_index": index,
                    "question_type": pair.get("question_type") or "",
                    "question": question,
                    "answer": answer,
                    "reasoning": reasoning,
                    "has_reasoning": int(bool(reasoning)),
                    "question_chars": len(question),
                    "answer_chars": len(answer),
                    # The prompt forbids Latin script outright, so any Latin
                    # letter in an answer is a concrete, checkable violation
                    # -- sort on this column to find leakage fast.
                    "latin_chars_in_answer": len(LATIN_RE.findall(answer)),
                    "context": "",
                    "context_chars": 0,
                    "model": record.get("model", ""),
                    "prompt_version": record.get("prompt_version", ""),
                    "ts": record.get("ts", ""),
                }


def load_contexts(chunk_ids: set, max_chars: int) -> dict:
    """Chunk text for just the chunk_ids that were actually generated for.
    Loading all of chunks.jsonl costs ~1.1GB RSS; at current progress only a
    fraction of it is needed, so filter while streaming."""
    if not cfg.CHUNKS_PATH.is_file():
        sys.exit(f"{cfg.CHUNKS_PATH} does not exist; run build_chunks.py first.")
    texts = {}
    with cfg.CHUNKS_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            chunk_id = row["chunk_id"]
            if chunk_id in chunk_ids:
                text = row["text"]
                texts[chunk_id] = text if max_chars <= 0 else text[:max_chars]
    return texts


def stratified_sample(rows: list, per_cell: int) -> list:
    """`per_cell` rows from each (source, question_type) cell. Sampling the
    flat list instead would just reproduce the corpus skew -- two telegram
    sources are over half of all chunks -- and a reviewer would read almost
    nothing from the smaller ones."""
    cells = defaultdict(list)
    for row in rows:
        cells[(row["source"], row["question_type"])].append(row)
    rng = random.Random(SAMPLE_SEED)
    out = []
    for key in sorted(cells):
        bucket = cells[key]
        out.extend(bucket if len(bucket) <= per_cell else rng.sample(bucket, per_cell))
    out.sort(key=lambda r: (r["source"], r["question_type"], r["chunk_id"], r["pair_index"]))
    return out


def write_csv(path, rows) -> int:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        count = 0
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--per-cell", type=int, default=60,
                        help="rows per (source, question_type) cell in the sample "
                             "file (default: 60, so ~40 cells give ~2,400 rows)")
    parser.add_argument("--context-chars", type=int, default=0,
                        help="truncate each context to this many characters; 0 (the "
                             "default) keeps the full chunk text")
    parser.add_argument("--no-full", action="store_true",
                        help="write only the stratified sample, skipping the large "
                             "all-pairs file")
    args = parser.parse_args()

    print("Reading generation records ...")
    rows = list(iter_pairs())
    if not rows:
        sys.exit("No completed generation records found; run generate_qa_openrouter.py first.")
    print(f"  {len(rows):,} QA pairs from "
          f"{len({r['chunk_id'] for r in rows}):,} chunks")

    print("Resolving context text from chunks.jsonl ...")
    contexts = load_contexts({r["chunk_id"] for r in rows}, args.context_chars)
    missing = 0
    for row in rows:
        text = contexts.get(row["chunk_id"])
        if text is None:
            missing += 1
            continue
        row["context"] = text
        row["context_chars"] = len(text)
    if missing:
        # Expected and harmless: chunk boundaries shifted when the corpus was
        # rebuilt after the character-corruption fix, orphaning a few older
        # generation records. See README, "A third corruption class".
        print(f"  {missing:,} pairs reference a chunk_id no longer in chunks.jsonl "
              f"(context left blank)")

    cfg.DATASET_DIR.mkdir(parents=True, exist_ok=True)

    sample = stratified_sample(rows, args.per_cell)
    sample_path = cfg.DATASET_DIR / "qa_review_sample.csv"
    write_csv(sample_path, sample)
    print(f"Wrote {len(sample):,} rows to {sample_path} "
          f"({sample_path.stat().st_size / 1e6:.1f} MB)")

    if not args.no_full:
        full_path = cfg.DATASET_DIR / "qa_review_all.csv"
        write_csv(full_path, rows)
        print(f"Wrote {len(rows):,} rows to {full_path} "
              f"({full_path.stat().st_size / 1e6:.1f} MB)")

    by_type = Counter(r["question_type"] for r in rows)
    by_source = Counter(r["source"] for r in rows)
    with_reasoning = sum(r["has_reasoning"] for r in rows)
    with_latin = sum(1 for r in rows if r["latin_chars_in_answer"])
    print("\nquestion_type:", dict(by_type.most_common()))
    print("source:", dict(by_source.most_common()))
    print(f"with reasoning: {with_reasoning:,} ({with_reasoning / len(rows) * 100:.1f}%)")
    print(f"answers containing Latin letters: {with_latin:,} "
          f"({with_latin / len(rows) * 100:.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
