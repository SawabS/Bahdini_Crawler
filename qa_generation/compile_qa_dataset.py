#!/usr/bin/env python3
"""Assemble generated QA pairs into the agreed fine-tuning JSONL schema.

Reads every qa_generation/output/generations/<source>/<document_id>.jsonl
record produced by generate_qa_openrouter.py (status "ok") and the matching
chunk text from qa_generation/output/chunks.jsonl, and writes, under
qa_generation/output/dataset/:

  qa_pairs.jsonl   one record per QA pair, in the schema confirmed with the
                   partner over email: a "messages" list (system/user/
                   assistant) plus a "metadata" object (document_id,
                   chunk_id, source, question_type)
  sample.jsonl     the first --sample-size records (default 20), to send for
                   review before generating/handing over the full dataset
  report.md        counts per source/question_type and a record-length
                   sanity check against the ~1,000-token/record budget

Run inside the conda "ai" env:
    python3 qa_generation/compile_qa_dataset.py
    python3 qa_generation/compile_qa_dataset.py --sample-size 20
"""

import argparse
import json
import sys
from collections import Counter

import gemma_tokenizer as gtok
import qa_config as cfg

# +/- allowance around the partner's ~1,000 token/record estimate before a
# record gets flagged in the report as worth a second look
RECORD_TOKEN_FLAG_THRESHOLD = 1200


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


def build_record(chunk_text: str, pair: dict, generation: dict) -> dict:
    user_content = f"Context: {chunk_text}\n\nQuestion: {pair['question']}"
    return {
        "messages": [
            {"role": "system", "content": cfg.QA_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": pair["answer"]},
        ],
        "metadata": {
            "document_id": generation["document_id"],
            "chunk_id": generation["chunk_id"],
            "source": generation["source"],
            "question_type": pair["question_type"],
        },
    }


def record_token_count(record: dict) -> int:
    """Real token count as the model will actually see it (chat-template
    rendered, BOS + turn markers included) when the tokenizer is available;
    falls back to the char-based estimate otherwise -- see gemma_tokenizer.py."""
    return gtok.count_chat_tokens(record["messages"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample-size", type=int, default=20,
                        help="number of records to also write to sample.jsonl (default: 20)")
    args = parser.parse_args()

    chunk_texts = load_chunk_texts()
    cfg.DATASET_DIR.mkdir(parents=True, exist_ok=True)

    if gtok.available():
        print(f"Using the real {cfg.GEMMA_TOKENIZER_MODEL} tokenizer for record token counts.")
    else:
        print(f"Real tokenizer unavailable; falling back to the "
              f"{cfg.CHARS_PER_TOKEN} chars/token estimate.")

    records = []
    by_source = Counter()
    by_question_type = Counter()
    over_threshold = 0
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
            record = build_record(chunk_text, pair, generation)
            token_estimate = record_token_count(record)
            if token_estimate > RECORD_TOKEN_FLAG_THRESHOLD:
                over_threshold += 1
            records.append(record)
            by_source[generation["source"]] += 1
            by_question_type[pair["question_type"]] += 1

    if not records:
        sys.exit("No completed generation records found; run generate_qa_openrouter.py first.")

    with open(cfg.DATASET_DIR / "qa_pairs.jsonl", "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    with open(cfg.DATASET_DIR / "sample.jsonl", "w", encoding="utf-8") as handle:
        for record in records[: args.sample_size]:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    lines = ["# QA dataset", ""]
    lines.append(f"QA pairs: {len(records)}  |  source chunks missing from chunks.jsonl: "
                 f"{missing_chunks}  |  records over {RECORD_TOKEN_FLAG_THRESHOLD}-token "
                 f"flag threshold: {over_threshold}")
    lines += ["", "| source | pairs |", "|---|---|"]
    for source in sorted(by_source):
        lines.append(f"| {source} | {by_source[source]} |")
    lines += ["", "| question_type | pairs |", "|---|---|"]
    for qtype in sorted(by_question_type):
        lines.append(f"| {qtype} | {by_question_type[qtype]} |")
    (cfg.DATASET_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {len(records)} QA pairs to {cfg.DATASET_DIR / 'qa_pairs.jsonl'}")
    print(f"Sample ({min(args.sample_size, len(records))} records): "
          f"{cfg.DATASET_DIR / 'sample.jsonl'}")
    print(f"Report: {cfg.DATASET_DIR / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
