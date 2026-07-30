#!/usr/bin/env python3
"""Assemble generated QA pairs into the agreed fine-tuning JSONL schema.

Reads every qa_generation/output/generations/<source>/<document_id>.jsonl
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

Run inside the conda "ai" env:
    python3 qa_generation/compile_qa_dataset.py
    python3 qa_generation/compile_qa_dataset.py --sample-size 20
"""

import argparse
import json
import random
import sys
from collections import Counter, defaultdict

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


def load_chunk_texts() -> dict:
    if not cfg.CHUNKS_PATH.is_file():
        sys.exit(f"{cfg.CHUNKS_PATH} does not exist; run build_chunks.py first.")
    texts = {}
    with open(cfg.CHUNKS_PATH, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            texts[row["chunk_id"]] = row["text"]
    return texts


def iter_generation_records():
    for path in sorted(cfg.GENERATIONS_DIR.glob("*/*.jsonl")):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


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


def build_sample(records: list, sample_size: int) -> list:
    """Round-robin across every (question_type, context_mode) combination
    present, so a small sample still shows the partner every shape being
    generated instead of whatever happened to come first."""
    groups = defaultdict(list)
    for record in records:
        meta = record["metadata"]
        groups[(meta["question_type"], meta["context_mode"])].append(record)
    group_keys = sorted(groups)

    sample = []
    idx = 0
    while len(sample) < sample_size and any(groups[key] for key in group_keys):
        key = group_keys[idx % len(group_keys)]
        if groups[key]:
            sample.append(groups[key].pop(0))
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
    args = parser.parse_args()

    chunk_texts = load_chunk_texts()
    cfg.DATASET_DIR.mkdir(parents=True, exist_ok=True)

    if gtok.available():
        print(f"Using the real {cfg.GEMMA_TOKENIZER_MODEL} tokenizer for record token counts.")
    else:
        print(f"Real tokenizer unavailable; falling back to the "
              f"{cfg.CHARS_PER_TOKEN} chars/token estimate.")

    rng = random.Random(CONTEXT_MODE_SEED)
    records = []
    by_source = Counter()
    by_question_type = Counter()
    by_context_mode = Counter()
    reasoning_count = 0
    over_threshold = 0
    prompt_token_total = 0
    missing_chunks = 0
    seen_chunk_ids = set()

    for generation in iter_generation_records():
        if generation.get("status") != "ok":
            continue
        chunk_id = generation["chunk_id"]
        if chunk_id in seen_chunk_ids:
            continue  # a resumed/re-run chunk may be recorded more than once
        seen_chunk_ids.add(chunk_id)
        chunk_text = chunk_texts.get(chunk_id)
        if chunk_text is None:
            missing_chunks += 1
            continue
        for pair in generation.get("qa_pairs", []):
            include_context = rng.random() < args.context_ratio
            record = build_record(chunk_text, pair, generation, include_context)
            prompt_tokens = record_prompt_tokens(record)
            prompt_token_total += prompt_tokens
            if prompt_tokens > PROMPT_TOKEN_FLAG_THRESHOLD:
                over_threshold += 1
            if "reasoning" in record["messages"][-1]:
                reasoning_count += 1
            records.append(record)
            by_source[generation["source"]] += 1
            by_question_type[pair["question_type"]] += 1
            by_context_mode[record["metadata"]["context_mode"]] += 1

    if not records:
        sys.exit("No completed generation records found; run generate_qa_openrouter.py first.")

    with open(cfg.DATASET_DIR / "qa_pairs.jsonl", "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    sample = build_sample(records, args.sample_size)
    with open(cfg.DATASET_DIR / "sample.jsonl", "w", encoding="utf-8") as handle:
        for record in sample:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    avg_prompt_tokens = round(prompt_token_total / len(records))
    lines = ["# QA dataset", ""]
    lines.append(f"QA pairs: {len(records)}  |  source chunks missing from chunks.jsonl: "
                 f"{missing_chunks}  |  with reasoning: {reasoning_count}  |  "
                 f"mean prompt tokens (system+question+context): {avg_prompt_tokens}  |  "
                 f"over {PROMPT_TOKEN_FLAG_THRESHOLD}-token flag threshold: {over_threshold}")
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

    print(f"Wrote {len(records)} QA pairs to {cfg.DATASET_DIR / 'qa_pairs.jsonl'}")
    print(f"Sample ({len(sample)} records, all question_type/context_mode combos "
          f"represented where available): {cfg.DATASET_DIR / 'sample.jsonl'}")
    print(f"Report: {cfg.DATASET_DIR / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
