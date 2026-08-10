#!/usr/bin/env python3
"""Measure the finished dataset once and persist the numbers.

Tokenizing ~950k records with the real Gemma tokenizer takes ~25 minutes.
Doing that inside a notebook, or again for a report, wastes half an hour
every time someone wants a chart. This computes everything in one pass and
writes output/dataset/stats.json; the notebook and any report read that.

Token counts are real, from google/gemma-4-31B-it via gemma_tokenizer, not
a chars/token estimate. Counted per message field, batched -- one tokenizer
call per batch rather than per string, which is the difference between ~25
minutes and several hours.

What "total tokens" means here, since it is the number that gets quoted:
the sum of the content of every message, plus the chat template's own
per-record overhead measured directly on a sample rather than guessed. That
is what one epoch of LoRA fine-tuning actually reads.

Run inside the conda "ai" env, from the repo root:
    python3 qa_generation/compute_dataset_stats.py
"""

import json
import statistics
import sys
from pathlib import Path
import time
from collections import Counter, defaultdict

import numpy as np

# qa_config and gemma_tokenizer live one level up, in qa_generation/, and
# are shared by every stage. Adding the parent explicitly keeps these
# runnable as plain scripts from anywhere -- `python3 qa_generation/export/
# export_outliers.py` -- rather than only from their own directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gemma_tokenizer as gtok
import qa_config as cfg

BATCH = 2_000
TEMPLATE_OVERHEAD_SAMPLE = 400      # records used to measure chat-template cost


def percentiles(values: np.ndarray) -> dict:
    if not len(values):
        return {}
    return {
        "min": int(values.min()),
        "p05": int(np.percentile(values, 5)),
        "p25": int(np.percentile(values, 25)),
        "median": int(np.percentile(values, 50)),
        "mean": round(float(values.mean()), 1),
        "p75": int(np.percentile(values, 75)),
        "p95": int(np.percentile(values, 95)),
        "p99": int(np.percentile(values, 99)),
        "max": int(values.max()),
    }


def histogram(values: np.ndarray, bins: int = 60) -> dict:
    """Binned counts, so a notebook can draw a distribution without
    shipping a 950k-element array into the chart."""
    if not len(values):
        return {"edges": [], "counts": []}
    hi = float(np.percentile(values, 99.5))          # clip the long tail
    counts, edges = np.histogram(values, bins=bins, range=(0, max(hi, 1)))
    return {"edges": [round(e, 1) for e in edges.tolist()],
            "counts": counts.tolist(),
            "clipped_above": int((values > hi).sum())}


def measure_template_overhead(records: list) -> float:
    """Tokens the chat template adds beyond raw message content: BOS, turn
    markers, the thought-channel wrapper. Measured, not assumed."""
    if not gtok.available():
        return 0.0
    deltas = []
    for record in records:
        rendered = gtok.count_chat_tokens(record["messages"])
        raw = sum(gtok.count_tokens(m["content"]) for m in record["messages"])
        raw += sum(gtok.count_tokens(m["reasoning"])
                   for m in record["messages"] if m.get("reasoning"))
        deltas.append(rendered - raw)
    return round(statistics.mean(deltas), 2)


def main() -> int:
    src = cfg.DATASET_DIR / "qa_pairs.jsonl"
    if not src.is_file():
        sys.exit(f"{src} does not exist; run compile_qa_dataset.py first.")
    if not gtok.available():
        print("WARNING: real tokenizer unavailable; counts will be char estimates.",
              file=sys.stderr)

    print("Pass 1: tokenizing every record ...")
    started = time.time()

    user_tokens, assistant_tokens, reasoning_tokens, system_tokens = [], [], [], []
    by_source = defaultdict(lambda: {"pairs": 0, "tokens": 0})
    by_qtype = defaultdict(lambda: {"pairs": 0, "tokens": 0})
    by_mode = defaultdict(lambda: {"pairs": 0, "tokens": 0})
    chunks, documents = set(), set()
    system_cache = {}
    template_sample = []
    n = 0

    batch_meta, batch_user, batch_assistant, batch_reasoning = [], [], [], []

    def flush():
        nonlocal batch_meta, batch_user, batch_assistant, batch_reasoning
        if not batch_meta:
            return
        u = gtok.count_tokens_batch(batch_user)
        a = gtok.count_tokens_batch(batch_assistant)
        # Only non-empty reasoning strings are sent to the tokenizer; the
        # empties are zeros by definition and would be ~47% wasted work.
        idx = [i for i, t in enumerate(batch_reasoning) if t]
        r = [0] * len(batch_reasoning)
        if idx:
            for i, count in zip(idx, gtok.count_tokens_batch([batch_reasoning[i] for i in idx])):
                r[i] = count
        for meta, ut, at, rt in zip(batch_meta, u, a, r):
            user_tokens.append(ut)
            assistant_tokens.append(at)
            reasoning_tokens.append(rt)
            st = system_cache[meta["system"]]
            system_tokens.append(st)
            total = ut + at + rt + st
            by_source[meta["source"]]["pairs"] += 1
            by_source[meta["source"]]["tokens"] += total
            by_qtype[meta["question_type"]]["pairs"] += 1
            by_qtype[meta["question_type"]]["tokens"] += total
            by_mode[meta["context_mode"]]["pairs"] += 1
            by_mode[meta["context_mode"]]["tokens"] += total
        batch_meta, batch_user, batch_assistant, batch_reasoning = [], [], [], []

    with src.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            n += 1
            messages = {m["role"]: m for m in record["messages"]}
            meta = record["metadata"]
            chunks.add(meta["chunk_id"])
            documents.add(meta["document_id"])

            system = messages["system"]["content"]
            if system not in system_cache:      # only two distinct strings exist
                system_cache[system] = gtok.count_tokens(system)

            if len(template_sample) < TEMPLATE_OVERHEAD_SAMPLE and n % 977 == 0:
                template_sample.append(record)

            batch_meta.append({
                "system": system, "source": meta["source"],
                "question_type": meta["question_type"],
                "context_mode": meta["context_mode"],
            })
            batch_user.append(messages["user"]["content"])
            batch_assistant.append(messages["assistant"]["content"])
            batch_reasoning.append(messages["assistant"].get("reasoning") or "")

            if len(batch_meta) >= BATCH:
                flush()
                if n % 100_000 < BATCH:
                    rate = n / (time.time() - started)
                    print(f"  {n:,} records ({time.time() - started:.0f}s, "
                          f"{rate:,.0f}/s, eta {(952801 - n) / rate / 60:.0f}m)")
    flush()

    print("Pass 2: measuring chat-template overhead ...")
    overhead = measure_template_overhead(template_sample)
    print(f"  {overhead} tokens per record, from {len(template_sample)} samples")

    user = np.array(user_tokens, dtype=np.int32)
    assistant = np.array(assistant_tokens, dtype=np.int32)
    reasoning = np.array(reasoning_tokens, dtype=np.int32)
    system = np.array(system_tokens, dtype=np.int32)
    per_record = user + assistant + reasoning + system

    content_total = int(per_record.sum())
    grand_total = content_total + int(round(overhead * n))

    stats = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tokenizer": cfg.GEMMA_TOKENIZER_MODEL,
        "tokenizer_real": gtok.available(),
        "counts": {
            "records": n,
            "unique_chunks": len(chunks),
            "unique_documents": len(documents),
            "sources": len(by_source),
            "with_reasoning": int((reasoning > 0).sum()),
        },
        "tokens": {
            "total_content": content_total,
            "template_overhead_per_record": overhead,
            "total_with_template": grand_total,
            "system": int(system.sum()),
            "user": int(user.sum()),
            "assistant": int(assistant.sum()),
            "reasoning": int(reasoning.sum()),
        },
        "distributions": {
            "user_tokens": percentiles(user),
            "assistant_tokens": percentiles(assistant),
            "reasoning_tokens": percentiles(reasoning[reasoning > 0]),
            "record_tokens": percentiles(per_record),
        },
        "histograms": {
            "user_tokens": histogram(user),
            "assistant_tokens": histogram(assistant),
            "record_tokens": histogram(per_record),
        },
        "by_source": {k: v for k, v in sorted(
            by_source.items(), key=lambda kv: -kv[1]["pairs"])},
        "by_question_type": {k: v for k, v in sorted(
            by_qtype.items(), key=lambda kv: -kv[1]["pairs"])},
        "by_context_mode": dict(by_mode),
    }

    out = cfg.DATASET_DIR / "stats.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDone in {(time.time() - started) / 60:.1f} min -> {out}")
    print(f"  records          {n:,}")
    print(f"  total tokens     {grand_total:,}  ({grand_total / 1e6:.1f}M)")
    print(f"    user           {int(user.sum()):,}")
    print(f"    assistant      {int(assistant.sum()):,}")
    print(f"    reasoning      {int(reasoning.sum()):,}")
    print(f"    system         {int(system.sum()):,}")
    print(f"    template       {int(round(overhead * n)):,}")
    print(f"  mean per record  {per_record.mean():.0f} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
