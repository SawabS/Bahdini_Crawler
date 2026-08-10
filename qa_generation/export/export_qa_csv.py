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

Everything streams, for the same reason compile_qa_dataset.py does. The
first version held every row in a list and every chunk's text in a dict,
which was fine at the 158k pairs that existed when it was written and is
not fine at the full corpus's ~950k -- that is several GB of live objects
on a machine with about a gigabyte free. Now chunks.jsonl is indexed by
byte offset and re-read on demand, rows are written as they are produced,
and the stratified sample is built with per-cell reservoir sampling so it
never needs the population in memory.

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
from pathlib import Path
import time
from collections import Counter, defaultdict

# qa_config and gemma_tokenizer live one level up, in qa_generation/, and
# are shared by every stage. Adding the parent explicitly keeps these
# runnable as plain scripts from anywhere -- `python3 qa_generation/export/
# export_outliers.py` -- rather than only from their own directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


def build_chunk_offset_index() -> dict:
    """chunk_id -> byte offset in chunks.jsonl (~40MB), so each context can
    be re-read on demand instead of holding the whole corpus. Same approach
    and same fast field slice as compile_qa_dataset.py."""
    if not cfg.CHUNKS_PATH.is_file():
        sys.exit(f"{cfg.CHUNKS_PATH} does not exist; run build_chunks.py first.")
    index = {}
    with cfg.CHUNKS_PATH.open(encoding="utf-8") as handle:
        offset = handle.tell()
        line = handle.readline()
        while line:
            next_offset = handle.tell()
            try:
                key_end = line.index('"chunk_id"') + 10
                value_start = line.index('"', key_end) + 1
                index[line[value_start:line.index('"', value_start)]] = offset
            except ValueError:
                index[json.loads(line)["chunk_id"]] = offset
            offset = next_offset
            line = handle.readline()
    return index


class CellReservoir:
    """Per-(source, question_type) reservoir sample of fixed size.

    Sampling the flat stream instead would just reproduce the corpus skew --
    two telegram sources are over half of all chunks -- and a reviewer would
    read almost nothing from the smaller ones. Reservoir rather than
    `random.sample` over a collected list because the population is ~950k
    rows carrying full context and does not fit in memory; this keeps at
    most `per_cell` rows per cell, chosen uniformly, in a single pass."""

    def __init__(self, per_cell: int, seed: int = SAMPLE_SEED):
        self.per_cell = per_cell
        self.rng = random.Random(seed)
        self.buckets = defaultdict(list)
        self.counts = Counter()

    def offer(self, row: dict) -> None:
        key = (row["source"], row["question_type"])
        self.counts[key] += 1
        bucket = self.buckets[key]
        if len(bucket) < self.per_cell:
            bucket.append(row)
            return
        # Classic algorithm R: the n-th item replaces a uniformly chosen
        # slot with probability per_cell/n, which leaves every item seen so
        # far equally likely to be held.
        j = self.rng.randrange(self.counts[key])
        if j < self.per_cell:
            bucket[j] = row

    def rows(self) -> list:
        out = [row for key in sorted(self.buckets) for row in self.buckets[key]]
        out.sort(key=lambda r: (r["source"], r["question_type"], r["chunk_id"], r["pair_index"]))
        return out


def open_csv(path):
    handle = path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(handle, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    return handle, writer


def write_csv(path, rows) -> int:
    handle, writer = open_csv(path)
    with handle:
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

    cfg.DATASET_DIR.mkdir(parents=True, exist_ok=True)

    print("Indexing chunks.jsonl by byte offset ...")
    offsets = build_chunk_offset_index()
    print(f"  {len(offsets):,} chunks indexed")

    reservoir = CellReservoir(args.per_cell)
    by_type = Counter()
    by_source = Counter()
    total = missing = with_reasoning = with_latin = 0

    full_path = cfg.DATASET_DIR / "qa_review_all.csv"
    full_handle = full_writer = None
    if not args.no_full:
        full_handle, full_writer = open_csv(full_path)

    print("Streaming generation records ...")
    started = time.time()
    try:
        with cfg.CHUNKS_PATH.open(encoding="utf-8") as chunks_handle:
            current_id = None
            current_text = ""
            for row in iter_pairs():
                # Pairs arrive grouped by chunk (4 per chunk), so caching the
                # last one turns 4 seeks into 1.
                if row["chunk_id"] != current_id:
                    current_id = row["chunk_id"]
                    offset = offsets.get(current_id)
                    if offset is None:
                        # Expected and harmless: chunk boundaries shifted when
                        # the corpus was rebuilt after the character-corruption
                        # fix, orphaning a few older generation records. See
                        # README, "A third corruption class".
                        current_text = None
                    else:
                        chunks_handle.seek(offset)
                        text = json.loads(chunks_handle.readline())["text"]
                        current_text = text if args.context_chars <= 0 \
                            else text[:args.context_chars]

                if current_text is None:
                    missing += 1
                else:
                    row["context"] = current_text
                    row["context_chars"] = len(current_text)

                total += 1
                by_type[row["question_type"]] += 1
                by_source[row["source"]] += 1
                with_reasoning += row["has_reasoning"]
                with_latin += 1 if row["latin_chars_in_answer"] else 0

                reservoir.offer(row)
                if full_writer is not None:
                    full_writer.writerow(row)

                if total % 200_000 == 0:
                    print(f"  {total:,} pairs ({time.time() - started:.0f}s)")
    finally:
        if full_handle is not None:
            full_handle.close()

    if not total:
        sys.exit("No completed generation records found; run generate_qa_openrouter.py first.")
    if missing:
        print(f"  {missing:,} pairs reference a chunk_id no longer in chunks.jsonl "
              f"(context left blank)")

    sample_path = cfg.DATASET_DIR / "qa_review_sample.csv"
    sample = reservoir.rows()
    write_csv(sample_path, sample)
    print(f"\nWrote {len(sample):,} rows to {sample_path} "
          f"({sample_path.stat().st_size / 1e6:.1f} MB)")
    if not args.no_full:
        print(f"Wrote {total:,} rows to {full_path} "
              f"({full_path.stat().st_size / 1e6:.1f} MB)")

    print("\nquestion_type:", dict(by_type.most_common()))
    print("source:", dict(by_source.most_common()))
    print(f"with reasoning: {with_reasoning:,} ({with_reasoning / total * 100:.1f}%)")
    print(f"answers containing Latin letters: {with_latin:,} "
          f"({with_latin / total * 100:.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
