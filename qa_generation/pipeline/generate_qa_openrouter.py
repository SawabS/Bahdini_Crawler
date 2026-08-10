#!/usr/bin/env python3
"""Generate QA pairs for each chunk in qa_generation/output/chunks.jsonl via
Gemini over OpenRouter.

Resumable like gemini_ocr_pipeline/run_ocr_openrouter.py: one record per
processed chunk is appended to
    qa_generation/output/generations/<source>/<document_id>.jsonl
and a re-run skips chunk_ids already recorded there, so it is safe to
interrupt (Ctrl-C) or cap with --budget-usd and continue later.

This script only produces the raw {question, answer, question_type,
reasoning} pairs per chunk; run compile_qa_dataset.py afterwards to
assemble the final messages+metadata JSONL agreed with the partner.

Run inside the conda "ai" env, from the repo root:
    python3 qa_generation/generate_qa_openrouter.py --max-chunks 20   # a quick sample
    python3 qa_generation/generate_qa_openrouter.py --budget-usd 25 --concurrency 16
"""

import argparse
import asyncio
import json
import random
import re
import sys
import time
from datetime import datetime, timezone

import aiohttp

import qa_config as cfg

FENCE_RE = re.compile(r"^```[a-zA-Z]*\n|\n?```$")
RETRY_ATTEMPTS = 5


def index_chunks(sources=None, origins=None):
    """Single streaming pass over chunks.jsonl (~700MB, 246k rows) that
    keeps only lightweight per-chunk metadata plus a byte offset, not the
    chunk text -- materializing every row's full text at once (measured:
    ~1.1GB RSS for the whole file) is unnecessary and, on a machine already
    under memory pressure, a real OOM risk for a run meant to stay alive for
    hours. Full records (with text) are re-read on demand, one batch at a
    time, in fetch_batch() below."""
    if not cfg.CHUNKS_PATH.is_file():
        sys.exit(f"{cfg.CHUNKS_PATH} does not exist; run build_chunks.py first.")
    index = []
    with open(cfg.CHUNKS_PATH, encoding="utf-8") as handle:
        offset = handle.tell()
        line = handle.readline()
        while line:
            next_offset = handle.tell()
            row = json.loads(line)
            if not (sources and row["source"] not in sources) and \
                    not (origins and row["origin"] not in origins):
                index.append({
                    "chunk_id": row["chunk_id"], "document_id": row["document_id"],
                    "source": row["source"], "origin": row["origin"], "offset": offset,
                })
            offset = next_offset
            line = handle.readline()
    return index


def fetch_batch(entries):
    """Re-read full records (including text) for a batch of index entries,
    via seek(), so at most `batch_size` chunks' worth of text is ever in
    memory at once instead of the whole corpus."""
    with open(cfg.CHUNKS_PATH, encoding="utf-8") as handle:
        out = []
        for entry in entries:
            handle.seek(entry["offset"])
            out.append(json.loads(handle.readline()))
        return out


def done_chunk_ids(source: str, origin: str, document_id: str) -> set:
    path = cfg.GENERATIONS_DIR / source / f"{origin}-{document_id}.jsonl"
    if not path.is_file():
        return set()
    done = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") in ("ok", "empty"):
                done.add(row["chunk_id"])
    return done


def parse_qa_response(text: str):
    """(qa_pairs, status, error). qa_pairs only includes structurally valid
    entries; malformed individual entries are dropped, not fatal."""
    cleaned = FENCE_RE.sub("", text.strip()).strip()
    if not cleaned:
        return [], "empty", None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return [], "parse_error", f"invalid JSON: {exc}"
    if not isinstance(data, list):
        return [], "parse_error", "response JSON is not a list"

    pairs = []
    for item in data:
        if not isinstance(item, dict):
            continue
        question = item.get("question")
        answer = item.get("answer")
        qtype = item.get("question_type")
        if not question or not answer or qtype not in cfg.QUESTION_TYPES:
            continue
        reasoning = item.get("reasoning")
        reasoning = reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None
        pairs.append({
            "question": question, "answer": answer, "question_type": qtype,
            "reasoning": reasoning,
        })

    if not pairs:
        return [], ("empty" if data == [] else "parse_error"), None
    return pairs, "ok", None


async def call_openrouter(session, sem, api_key, prompt, state, model):
    payload = {
        "model": model,
        "temperature": 0.7,
        "max_tokens": cfg.MAX_OUTPUT_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_error = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            async with sem:
                async with session.post(
                        cfg.OPENROUTER_URL, headers=headers, json=payload,
                        timeout=aiohttp.ClientTimeout(total=180)) as resp:
                    if resp.status in (402, 403):
                        state["stop"] = True
                        body = (await resp.text())[:200]
                        return {"status": "error", "text": "",
                                "error": f"HTTP {resp.status} (OpenRouter credit "
                                         f"exhausted or key forbidden): {body}"}
                    if resp.status == 429 or resp.status >= 500:
                        body = (await resp.text())[:200]
                        raise RuntimeError(f"HTTP {resp.status}: {body}")
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as error:
            last_error = error
            await asyncio.sleep(2 * 2 ** attempt + random.uniform(0, 1))
            continue

        choice = data["choices"][0]
        text = (choice.get("message", {}).get("content") or "").strip()
        usage = data.get("usage") or {}
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        return {
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "est_cost_usd": round(
                cfg.estimate_cost_usd(input_tokens, output_tokens, model), 6),
        }
    return {"status": "error", "text": "",
            "error": f"{type(last_error).__name__}: {last_error}"}


async def process_chunk(chunk, sem, session, api_key, write_lock, state, args):
    if state["stop"]:
        return
    prompt = cfg.build_qa_prompt(chunk["text"], args.pairs_per_chunk)
    result = await call_openrouter(session, sem, api_key, prompt, state, args.model)

    record = {
        "chunk_id": chunk["chunk_id"],
        "document_id": chunk["document_id"],
        "source": chunk["source"],
        "model": args.model,
        "prompt_version": cfg.QA_PROMPT_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if "error" in result:
        record.update(status="error", error=result["error"], qa_pairs=[])
    else:
        pairs, status, error = parse_qa_response(result["text"])
        record.update(
            status=status, qa_pairs=pairs, error=error,
            input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
            est_cost_usd=result["est_cost_usd"],
        )
        totals = state["totals"]
        totals["input_tokens"] += result["input_tokens"]
        totals["output_tokens"] += result["output_tokens"]
        totals["est_cost_usd"] += result["est_cost_usd"]
        if totals["est_cost_usd"] >= state["budget"] and not state["stop"]:
            state["stop"] = True
            print(f"\n>>> budget cap (${state['budget']:.2f}) reached at "
                  f"${totals['est_cost_usd']:.4f}; finishing in-flight requests and "
                  f"stopping.\n")

    state["totals"][record["status"]] = state["totals"].get(record["status"], 0) + 1
    out_path = cfg.GENERATIONS_DIR / chunk["source"] / f"{chunk['origin']}-{chunk['document_id']}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    async with write_lock:
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write(line)
    n_pairs = len(record.get("qa_pairs", []))
    print(f"  {chunk['chunk_id']}: {record['status']} ({n_pairs} pairs)")


async def run(args) -> int:
    api_key = cfg.load_dotenv_key("OPENROUTER_API_KEY")
    index = index_chunks(sources=args.source, origins=args.origin)
    if not args.no_shuffle:
        random.Random(0).shuffle(index)

    done_cache = {}
    pending = []
    for entry in index:
        key = (entry["source"], entry["origin"], entry["document_id"])
        if key not in done_cache:
            done_cache[key] = done_chunk_ids(entry["source"], entry["origin"], entry["document_id"])
        if entry["chunk_id"] in done_cache[key]:
            continue
        pending.append(entry)
    if args.max_chunks:
        pending = pending[: args.max_chunks]

    print(f"{len(index)} chunks total, {len(pending)} pending after resuming from "
          f"{cfg.GENERATIONS_DIR}")
    if args.dry_run:
        print("--dry-run: not calling OpenRouter.")
        return 0
    if not pending:
        return 0

    state = {
        "totals": {"input_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0},
        "stop": False,
        "budget": args.budget_usd if args.budget_usd else float("inf"),
    }
    write_lock = asyncio.Lock()
    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.time()

    try:
        async with aiohttp.ClientSession() as session:
            for start in range(0, len(pending), args.batch_size):
                if state["stop"]:
                    break
                batch = fetch_batch(pending[start:start + args.batch_size])
                await asyncio.gather(*[
                    process_chunk(chunk, sem, session, api_key, write_lock, state, args)
                    for chunk in batch
                ])
    except KeyboardInterrupt:
        print("\nInterrupted; every finished chunk is already saved. Re-run to resume.")

    elapsed = time.time() - t0
    print("\nRun summary:")
    for key, value in sorted(state["totals"].items()):
        if key == "est_cost_usd":
            print(f"  {key}: ${value:.4f}")
        else:
            print(f"  {key}: {value}")
    print(f"  elapsed_s: {elapsed:.0f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", action="append", help="limit to one source (repeatable)")
    parser.add_argument("--origin", action="append",
                        help="limit to one chunk origin, e.g. safe_extraction (repeatable)")
    parser.add_argument("--max-chunks", type=int,
                        help="stop after processing this many pending chunks (use a small "
                             "number to produce a sample for review)")
    parser.add_argument("--budget-usd", type=float,
                        help="stop dispatching new requests once cumulative estimated "
                             "cost reaches this many dollars")
    parser.add_argument("--concurrency", type=int, default=8,
                        help="concurrent OpenRouter requests in flight (default: 8)")
    parser.add_argument("--batch-size", type=int, default=16,
                        help="chunks dispatched per gather() batch (default: 16)")
    parser.add_argument("--pairs-per-chunk", type=int, default=cfg.PAIRS_PER_CHUNK,
                        help=f"QA pairs requested per chunk (default: {cfg.PAIRS_PER_CHUNK})")
    parser.add_argument("--model", default=cfg.OPENROUTER_MODEL,
                        help=f"OpenRouter model slug, for A/B piloting a different tier "
                             f"without changing the pipeline default (default: {cfg.OPENROUTER_MODEL})")
    parser.add_argument("--no-shuffle", action="store_true",
                        help="process chunks in file order instead of a fixed shuffle "
                             "(shuffling spreads a --budget-usd or --max-chunks cap "
                             "across sources/documents)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report how many chunks are pending without calling OpenRouter")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
