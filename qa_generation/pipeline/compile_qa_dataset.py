#!/usr/bin/env python3
"""Assemble generated QA pairs into the agreed fine-tuning JSONL schema.

Reads every qa_generation/output/generations/<source>/<origin>-<doc_id>.jsonl
record produced by generate_qa_openrouter.py (status "ok") and the matching
chunk text from qa_generation/output/chunks.jsonl, and writes, under
qa_generation/output/dataset/:

  qa_pairs.jsonl   one record per QA pair, in the schema confirmed with the
                   partner over email: a "messages" list (system/user/
                   assistant) plus a "metadata" object (document_id,
                   chunk_id, source, question_type, context_mode). A
                   CONTEXT_RATIO share of records include a "Context: ..."
                   block in the user message (context_mode="with_context");
                   the rest are a bare question (context_mode="no_context"),
                   for the partner's two serving modes (retrieval vs. not).
                   When a pair's answer needs reasoning, the assistant
                   message carries a "reasoning" key alongside "content" --
                   Gemma 4's own chat template renders that into its native
                   thought channel, so reasoning never has to be inlined
                   into the answer text itself.
  sample.jsonl     up to --sample-size records (default 20), round-robined
                   across every (question_type, context_mode) combination
                   actually generated, so the partner can see every type in
                   one small file instead of whatever came first
  report.md        counts per source/question_type/context_mode, how many
                   have a reasoning field, and a prompt-length sanity check
                   against the ~1,000-token/record budget

Everything here streams. The full corpus is ~950k pairs and the finished
file is a few GB; the original version loaded all of chunks.jsonl into a
dict (~1.1GB) and accumulated every finished record in a list (~2.5GB)
before writing a byte, which does not fit alongside the rest of a 16GB
machine's working set. Instead:

  * chunks.jsonl is indexed by byte offset (~40MB) and each chunk's text is
    re-read on demand via seek(), the same trick generate_qa_openrouter.py
    uses on the same file;
  * finished records are written as they are built, never collected;
  * the sample keeps only a handful of records per (question_type,
    context_mode) cell, and the statistics are plain counters.

Run inside the conda "ai" env:
    python3 qa_generation/compile_qa_dataset.py
    python3 qa_generation/compile_qa_dataset.py --sample-size 20
"""

import argparse
import json
import random
import sys
from pathlib import Path
import time
from collections import Counter, defaultdict

# qa_config and gemma_tokenizer live one level up, in qa_generation/, and
# are shared by every stage. Adding the parent explicitly keeps these
# runnable as plain scripts from anywhere -- `python3 qa_generation/export/
# export_outliers.py` -- rather than only from their own directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gemma_tokenizer as gtok
import qa_config as cfg

# The partner's ~1,000-token budget is a mean covering system + question +
# context (the answer is excluded), and going over it in some cases is
# fine; this is the point past which a record is flagged as worth a look,
# not a hard limit.
PROMPT_TOKEN_FLAG_THRESHOLD = 1300

# Fixed seed so the with/without-context split is reproducible across runs
# over the same generation records, not reshuffled every time.
CONTEXT_MODE_SEED = 42

# Separate stream so that changing the token-check sample size can never
# perturb the context_mode assignment, which is part of the deliverable.
TOKEN_SAMPLE_SEED = 7

# Per (question_type, context_mode) cell, how many records to keep as
# sample candidates. build_sample round-robins across cells, so a few per
# cell is plenty for any realistic --sample-size and keeps memory flat.
SAMPLE_CANDIDATES_PER_CELL = 50


def build_chunk_offset_index() -> dict:
    """chunk_id -> byte offset in chunks.jsonl.

    ~40MB for 246k chunks, versus ~1.1GB to hold every chunk's text. The
    text is fetched per generation record in the main pass below."""
    if not cfg.CHUNKS_PATH.is_file():
        sys.exit(f"{cfg.CHUNKS_PATH} does not exist; run build_chunks.py first.")
    index = {}
    with open(cfg.CHUNKS_PATH, encoding="utf-8") as handle:
        offset = handle.tell()
        line = handle.readline()
        while line:
            next_offset = handle.tell()
            # Cheaper than json.loads on every one of 246k rows: chunk_id is
            # the first field, so slice it out directly. Written
            # whitespace-agnostically rather than against a literal
            # '"chunk_id": "' -- json.dumps' spacing is a formatting detail,
            # and matching it exactly would silently fall through to the slow
            # path if it ever changed. Any surprise still parses correctly.
            try:
                key_end = line.index('"chunk_id"') + 10
                value_start = line.index('"', key_end) + 1
                index[line[value_start:line.index('"', value_start)]] = offset
            except ValueError:
                index[json.loads(line)["chunk_id"]] = offset
            offset = next_offset
            line = handle.readline()
    return index


def iter_generation_records():
    for path in sorted(cfg.GENERATIONS_DIR.glob("*/*.jsonl")):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def count_pairs() -> tuple:
    """(ok_records, pairs) in one cheap pass, no chunk lookups.

    Used to size the token-check sample exactly and to give the main pass a
    progress denominator -- this compile takes minutes on the full corpus,
    and a silent multi-minute job is indistinguishable from a hung one."""
    records = pairs = 0
    seen = set()
    for generation in iter_generation_records():
        if generation.get("status") != "ok":
            continue
        chunk_id = generation["chunk_id"]
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        records += 1
        pairs += len(generation.get("qa_pairs") or [])
    return records, pairs


def build_record(chunk_text: str, pair: dict, generation: dict, include_context: bool) -> dict:
    if include_context:
        system_prompt = cfg.QA_SYSTEM_PROMPT
        user_content = f"Context: {chunk_text}\n\nQuestion: {pair['question']}"
        context_mode = "with_context"
    else:
        system_prompt = cfg.QA_SYSTEM_PROMPT_NO_CONTEXT
        user_content = f"Question: {pair['question']}"
        context_mode = "no_context"

    assistant_message = {"role": "assistant", "content": pair["answer"]}
    if pair.get("reasoning"):
        assistant_message["reasoning"] = pair["reasoning"]

    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
            assistant_message,
        ],
        "metadata": {
            "document_id": generation["document_id"],
            "chunk_id": generation["chunk_id"],
            "source": generation["source"],
            "question_type": pair["question_type"],
            "context_mode": context_mode,
        },
    }


def record_prompt_tokens(record: dict) -> int:
    """Tokens for system + question + context only, rendered exactly as
    Gemma would see them before generating -- what the partner's
    ~1,000-token budget is actually measured against. The answer is
    excluded; see gemma_tokenizer.count_prompt_tokens."""
    return gtok.count_prompt_tokens(record["messages"])


def build_sample(candidates: dict, sample_size: int) -> list:
    """Round-robin across every (question_type, context_mode) combination
    present, so a small sample still shows the partner every shape being
    generated instead of whatever happened to come first."""
    group_keys = sorted(candidates)
    sample = []
    idx = 0
    while len(sample) < sample_size and any(candidates[key] for key in group_keys):
        key = group_keys[idx % len(group_keys)]
        if candidates[key]:
            sample.append(candidates[key].pop(0))
        idx += 1
    return sample


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample-size", type=int, default=20,
                        help="number of records to also write to sample.jsonl (default: 20)")
    parser.add_argument("--context-ratio", type=float, default=cfg.CONTEXT_RATIO,
                        help=f"fraction of records delivered with a context block "
                             f"(default: {cfg.CONTEXT_RATIO})")
    parser.add_argument("--token-check-sample", type=int, default=25_000,
                        help="how many finished records to actually tokenize for the "
                             "prompt-length report (default: 25000). Tokenizing all "
                             "~950k means ~2M chat-template renders and takes hours, "
                             "for a QC statistic that a sample estimates fine. Pass 0 "
                             "to tokenize every record.")
    args = parser.parse_args()

    if gtok.available():
        print(f"Using the real {cfg.GEMMA_TOKENIZER_MODEL} tokenizer for record token counts.")
    else:
        print(f"Real tokenizer unavailable; falling back to the "
              f"{cfg.CHARS_PER_TOKEN} chars/token estimate.")

    print("Counting generated pairs ...")
    total_records, total_pairs = count_pairs()
    if not total_pairs:
        sys.exit("No completed generation records found; run generate_qa_openrouter.py first.")
    print(f"  {total_records:,} chunks with pairs, {total_pairs:,} QA pairs")

    print("Indexing chunks.jsonl by byte offset ...")
    offsets = build_chunk_offset_index()
    print(f"  {len(offsets):,} chunks indexed")

    cfg.DATASET_DIR.mkdir(parents=True, exist_ok=True)

    if args.token_check_sample and args.token_check_sample < total_pairs:
        token_rate = args.token_check_sample / total_pairs
    else:
        token_rate = 1.0

    rng = random.Random(CONTEXT_MODE_SEED)
    token_rng = random.Random(TOKEN_SAMPLE_SEED)
    by_source = Counter()
    by_question_type = Counter()
    by_context_mode = Counter()
    reasoning_count = 0
    over_threshold = 0
    prompt_token_total = 0
    tokenized = 0
    missing_chunks = 0
    written = 0
    seen_chunk_ids = set()
    candidates = defaultdict(list)

    out_path = cfg.DATASET_DIR / "qa_pairs.jsonl"
    started = time.time()
    print(f"Writing {out_path} ...")

    with open(cfg.CHUNKS_PATH, encoding="utf-8") as chunks_handle, \
            open(out_path, "w", encoding="utf-8") as out_handle:
        for generation in iter_generation_records():
            if generation.get("status") != "ok":
                continue
            chunk_id = generation["chunk_id"]
            if chunk_id in seen_chunk_ids:
                continue  # a resumed/re-run chunk may be recorded more than once
            seen_chunk_ids.add(chunk_id)

            offset = offsets.get(chunk_id)
            if offset is None:
                missing_chunks += 1
                continue
            chunks_handle.seek(offset)
            chunk_text = json.loads(chunks_handle.readline())["text"]

            for pair in generation.get("qa_pairs") or []:
                include_context = rng.random() < args.context_ratio
                record = build_record(chunk_text, pair, generation, include_context)

                if token_rng.random() < token_rate:
                    prompt_tokens = record_prompt_tokens(record)
                    prompt_token_total += prompt_tokens
                    tokenized += 1
                    if prompt_tokens > PROMPT_TOKEN_FLAG_THRESHOLD:
                        over_threshold += 1

                context_mode = record["metadata"]["context_mode"]
                if "reasoning" in record["messages"][-1]:
                    reasoning_count += 1
                by_source[generation["source"]] += 1
                by_question_type[pair["question_type"]] += 1
                by_context_mode[context_mode] += 1

                key = (pair["question_type"], context_mode)
                if len(candidates[key]) < SAMPLE_CANDIDATES_PER_CELL:
                    candidates[key].append(record)

                out_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
                if written % 100_000 == 0:
                    elapsed = time.time() - started
                    print(f"  {written:,}/{total_pairs:,} records "
                          f"({written / total_pairs * 100:.1f}%, {elapsed:.0f}s)")

    if not written:
        sys.exit("No records written; every generation record was missing its chunk text.")

    sample = build_sample(candidates, args.sample_size)
    with open(cfg.DATASET_DIR / "sample.jsonl", "w", encoding="utf-8") as handle:
        for record in sample:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    avg_prompt_tokens = round(prompt_token_total / tokenized) if tokenized else 0
    coverage = "every record" if tokenized >= written else f"a {tokenized:,}-record sample"
    over_pct = (over_threshold / tokenized * 100) if tokenized else 0.0

    lines = ["# QA dataset", ""]
    lines.append(f"QA pairs: {written}  |  source chunks missing from chunks.jsonl: "
                 f"{missing_chunks}  |  with reasoning: {reasoning_count}")
    lines.append("")
    lines.append(f"Prompt-length check measured over {coverage}: mean prompt tokens "
                 f"(system+question+context) {avg_prompt_tokens}, "
                 f"{over_pct:.2f}% over the {PROMPT_TOKEN_FLAG_THRESHOLD}-token flag "
                 f"threshold ({over_threshold:,} of {tokenized:,} measured).")
    lines += ["", "| source | pairs |", "|---|---|"]
    for source in sorted(by_source):
        lines.append(f"| {source} | {by_source[source]} |")
    lines += ["", "| question_type | pairs |", "|---|---|"]
    for qtype in sorted(by_question_type):
        lines.append(f"| {qtype} | {by_question_type[qtype]} |")
    lines += ["", "| context_mode | pairs |", "|---|---|"]
    for mode in sorted(by_context_mode):
        lines.append(f"| {mode} | {by_context_mode[mode]} |")
    (cfg.DATASET_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    size_gb = out_path.stat().st_size / 1e9
    print(f"\nWrote {written:,} QA pairs to {out_path} ({size_gb:.2f} GB) "
          f"in {time.time() - started:.0f}s")
    print(f"Sample ({len(sample)} records, all question_type/context_mode combos "
          f"represented where available): {cfg.DATASET_DIR / 'sample.jsonl'}")
    print(f"Report: {cfg.DATASET_DIR / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
