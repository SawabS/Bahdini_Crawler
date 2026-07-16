#!/usr/bin/env python3
"""OpenRouter-backed OCR runner over the raw crawl, no needs_ocr split.

Unlike run_ocr.py (which only OCRs documents that extract_pipeline.py flagged
needs_ocr, via Vertex AI), this walks every PDF under ocr_config.FULL_CRAWL_
SOURCES directly - zcks, the three telegram crawls, and pertokenbadini - and
sends every page to Gemini through OpenRouter's OpenAI-compatible API. A
document whose native text layer was "safe" and a document that's pure scans
are treated identically: every page gets rendered and OCR'd.

Records are appended to the same gemini_ocr_pipeline/output/pages/<source>/
<doc_id>.jsonl files run_ocr.py uses (doc_id is the same source+path hash),
so compile_corpus.py needs no changes and reruns still resume from whatever
is already recorded. Corrupt/unreadable PDFs (fitz reports 0 pages) get an
explicit page-0 "error" record instead of being silently dropped from the
queue.

Two levels of concurrency, both async (aiohttp), because rendering with
PyMuPDF is CPU-bound and the OpenRouter calls are I/O-bound:
  --doc-concurrency   documents being worked on at once
  --concurrency       OpenRouter requests in flight at once (the real
                       throughput knob)

--budget-usd stops dispatching new requests once cumulative estimated cost
crosses it (best-effort local tracking; OpenRouter's own per-key credit
limit is the hard backstop - a 402 response sets the same stop flag).

Run inside the conda "ai" env, from the repo root:
    conda run --no-capture-output -n ai python -u \
      gemini_ocr_pipeline/run_ocr_openrouter.py --budget-usd 9.5 --concurrency 24
"""

import argparse
import asyncio
import base64
import hashlib
import json
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime, timezone

import aiohttp
import fitz

import ocr_config as cfg
from run_ocr import BLANK_SPAN, DONE_STATUSES, consecutive_not_badini, page_statuses, render_page  # noqa: E402,F401

RETRY_ATTEMPTS = 5

# Rendering runs in a ProcessPoolExecutor, not threads. Early pilots crashed
# the whole run with a glibc "double free in tcache" from inside PyMuPDF -
# once at ~7,000 pages in, once within seconds, and a bare fitz.open() on the
# exact page blamed in the second crash rendered fine in isolation. That
# points to a rare, page-content-triggered crash somewhere in libmupdf's C
# code (not a Python-level bug we can catch with try/except) rather than one
# reliably-broken file. A worker process dying from that takes only itself
# down; render_in_executor() below detects a BrokenProcessPool, replaces it,
# and the run continues - one bad page costs a retry, not the whole job.


def discover_queue(sources) -> list:
    rows = []
    for source in sources:
        root = cfg.FULL_CRAWL_SOURCES[source]
        if not root.is_dir():
            print(f"  (skipping {source}: {root} does not exist)", file=sys.stderr)
            continue
        for path in sorted(root.rglob("*.pdf")):
            input_rel = path.relative_to(root).as_posix()
            rows.append({
                "source": source,
                "input": input_rel,
                "path": path,
                "doc_id": cfg.doc_id(source, input_rel),
            })
    return rows


def probe_pages(path):
    """(page_count, error_message_or_None); page_count 0 means unreadable.

    Runs in a worker process (see ProcessPoolExecutor in run()) - keep this
    function's arguments and return value picklable and free of shared state.
    """
    try:
        with fitz.open(path) as document:
            return len(document), None
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def render_one_page(path, page_number):
    """Runs in a worker process; see the note on probe_pages above."""
    with fitz.open(path) as document:
        return render_page(document, page_number - 1)


async def render_in_executor(state, args, func, *fargs):
    """loop.run_in_executor against state["executor"], surviving a worker
    process crashing outright: on BrokenProcessPool, swap in a fresh
    executor (only one caller does the actual swap) and retry once."""
    loop = asyncio.get_running_loop()
    for attempt in range(2):
        executor = state["executor"]
        try:
            return await loop.run_in_executor(executor, func, *fargs)
        except BrokenProcessPool:
            async with state["executor_lock"]:
                if state["executor"] is executor:
                    print("\n>>> a render worker process crashed (BrokenProcessPool); "
                          "replacing the pool and retrying.\n")
                    try:
                        executor.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                    state["executor"] = ProcessPoolExecutor(max_workers=args.render_workers)
    raise RuntimeError("render worker pool kept crashing after a retry")


async def write_record(write_lock, record_path, base, page, size, png_bytes, result):
    record = dict(base)
    record.update(
        page=page,
        image_sha256=hashlib.sha256(png_bytes).hexdigest() if png_bytes else "",
        image_px=list(size) if size else [0, 0],
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **result,
    )
    line = json.dumps(record, ensure_ascii=False) + "\n"
    async with write_lock:
        with open(record_path, "a", encoding="utf-8") as handle:
            handle.write(line)


def bump(state, status, result=None):
    totals = state["totals"]
    totals[status] = totals.get(status, 0) + 1
    if result:
        totals["input_tokens"] += result.get("input_tokens", 0)
        totals["output_tokens"] += result.get("output_tokens", 0)
        totals["est_cost_usd"] += result.get("est_cost_usd", 0.0)
        if totals["est_cost_usd"] >= state["budget"] and not state["stop"]:
            state["stop"] = True
            print(f"\n>>> budget cap (${state['budget']:.2f}) reached at "
                  f"${totals['est_cost_usd']:.4f}; finishing in-flight requests and stopping.\n")


async def call_openrouter(session, page_sem, api_key, png_bytes, state):
    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": cfg.OPENROUTER_MODEL,
        "temperature": 0,
        "max_tokens": cfg.MAX_OUTPUT_TOKENS,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": cfg.PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_error = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            async with page_sem:
                async with session.post(
                        cfg.OPENROUTER_URL, headers=headers, json=payload,
                        timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status in (402, 403):
                        # 402 is the documented "out of credit" response, but a
                        # key that has hit its hard cap has also been observed
                        # returning plain 403 Forbidden instead - treat both as
                        # a hard stop rather than retrying 5x per page against
                        # a key that is not going to start working again.
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
        finish_reason = choice.get("finish_reason")
        if text == cfg.NO_TEXT_MARKER:
            status = "no_text"
        elif text.startswith(cfg.NOT_BADINI_PREFIX):
            status = "not_badini"
        elif text:
            status = "ok"
        else:
            status = "empty"
        return {
            "status": status,
            "text": text,
            "finish_reason": finish_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "est_cost_usd": round(
                cfg.estimate_cost_usd_openrouter(input_tokens, output_tokens), 6),
        }
    return {"status": "error", "text": "", "error": f"{type(last_error).__name__}: {last_error}"}


async def handle_page(session, page_sem, api_key, write_lock, record_path,
                       base, page_number, statuses, state, row, args):
    if state["stop"]:
        return
    try:
        png_bytes, size, blank = await render_in_executor(
            state, args, render_one_page, row["path"], page_number)
    except Exception as exc:
        result = {"status": "error", "text": "",
                  "error": f"render failed: {type(exc).__name__}: {exc}"}
        await write_record(write_lock, record_path, base, page_number, None, b"", result)
        statuses[page_number] = "error"
        bump(state, "error")
        print(f"  {row['source']}/{row['input']} p{page_number}: error (render)")
        return

    if blank:
        await write_record(write_lock, record_path, base, page_number, size, png_bytes,
                            {"status": "blank", "text": ""})
        statuses[page_number] = "blank"
        bump(state, "blank")
        print(f"  {row['source']}/{row['input']} p{page_number}: blank")
        return

    if state["stop"]:
        return
    result = await call_openrouter(session, page_sem, api_key, png_bytes, state)
    await write_record(write_lock, record_path, base, page_number, size, png_bytes, result)
    statuses[page_number] = result["status"]
    bump(state, result["status"], result)
    print(f"  {row['source']}/{row['input']} p{page_number}: {result['status']}")


async def process_document(row, doc_sem, page_sem, session, api_key, state, args):
    async with doc_sem:
        if state["stop"]:
            return
        record_path = cfg.PAGES_DIR / row["source"] / f"{row['doc_id']}.jsonl"
        record_path.parent.mkdir(parents=True, exist_ok=True)
        statuses = page_statuses(record_path)
        if statuses.get(0) in ("doc_skipped", "error") and not args.ignore_doc_skips:
            return

        try:
            n_pages, error = await render_in_executor(state, args, probe_pages, row["path"])
        except Exception as exc:
            n_pages, error = 0, f"probe crashed the render worker: {exc}"
        if n_pages == 0:
            minimal = {"source": row["source"], "file": row["input"], "doc_id": row["doc_id"],
                       "model": cfg.OPENROUTER_MODEL, "prompt_version": cfg.PROMPT_VERSION,
                       "n_pages": 0}
            await write_record(state["write_lock"], record_path, minimal, 0, None, b"",
                                {"status": "error", "text": "",
                                 "error": error or "0-page/corrupt PDF"})
            bump(state, "error")
            print(f"  {row['source']}/{row['input']}: ERROR ({error or '0-page PDF'})")
            return

        done = {page for page, status in statuses.items() if status in DONE_STATUSES}
        pending = [page for page in range(1, n_pages + 1) if page not in done]
        if not pending:
            return

        base = {"source": row["source"], "file": row["input"], "doc_id": row["doc_id"],
                "model": cfg.OPENROUTER_MODEL, "prompt_version": cfg.PROMPT_VERSION,
                "n_pages": n_pages}

        for start in range(0, len(pending), args.batch_size):
            if state["stop"]:
                return
            batch = pending[start:start + args.batch_size]
            tasks = [
                handle_page(session, page_sem, api_key, state["write_lock"],
                            record_path, base, page_number, statuses, state, row, args)
                for page_number in batch
            ]
            await asyncio.gather(*tasks)

            if (args.skip_after and start + args.batch_size < len(pending)
                    and consecutive_not_badini(statuses) >= args.skip_after):
                skip_record = dict(base)
                skip_record.update(
                    page=0, status="doc_skipped",
                    reason=f"{args.skip_after} consecutive not_badini pages",
                    ts=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                async with state["write_lock"]:
                    with open(record_path, "a", encoding="utf-8") as handle:
                        handle.write(json.dumps(skip_record, ensure_ascii=False) + "\n")
                bump(state, "docs_skipped")
                print(f"  {row['source']}/{row['input']}: skipped rest of document "
                      f"(not Badini)")
                return


async def run(args) -> int:
    api_key = cfg.load_dotenv_key("OPENROUTER_API_KEY")
    sources = args.source or sorted(cfg.FULL_CRAWL_SOURCES)
    for source in sources:
        if source not in cfg.FULL_CRAWL_SOURCES:
            sys.exit(f"Unknown source {source!r}; choices: {sorted(cfg.FULL_CRAWL_SOURCES)}")

    t0 = time.time()
    rows = discover_queue(sources)
    if args.doc:
        rows = [row for row in rows if args.doc in row["input"]]
    if not args.no_shuffle:
        random.Random(0).shuffle(rows)
    if args.max_docs:
        rows = rows[: args.max_docs]
    print(f"Queued {len(rows)} documents across {sources} "
          f"({time.time() - t0:.1f}s to discover)")

    state = {
        "totals": {"input_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0},
        "stop": False,
        "budget": args.budget_usd if args.budget_usd else float("inf"),
        "write_lock": asyncio.Lock(),
        "executor": ProcessPoolExecutor(max_workers=args.render_workers),
        "executor_lock": asyncio.Lock(),
    }

    page_sem = asyncio.Semaphore(args.concurrency)
    doc_sem = asyncio.Semaphore(args.doc_concurrency)
    connector = aiohttp.TCPConnector(limit=args.concurrency + 8)

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [
                process_document(row, doc_sem, page_sem, session, api_key, state, args)
                for row in rows
            ]
            await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\nInterrupted; every finished page is already saved. Re-run to resume.")
    finally:
        state["executor"].shutdown(wait=True, cancel_futures=True)

    elapsed = time.time() - t0
    print("\nRun summary:")
    for key, value in sorted(state["totals"].items()):
        if key == "est_cost_usd":
            print(f"  {key}: ${value:.4f}")
        else:
            print(f"  {key}: {value}")
    print(f"  elapsed_s: {elapsed:.0f}")
    if state["stop"]:
        print(f"\nStopped early: budget cap (${state['budget']:.2f}) reached or "
              f"OpenRouter returned 402.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", action="append",
                        choices=sorted(cfg.FULL_CRAWL_SOURCES),
                        help="limit to one source (repeatable); default: all 5")
    parser.add_argument("--doc", help="only documents whose input path contains this substring")
    parser.add_argument("--max-docs", type=int, help="stop after queuing this many documents")
    parser.add_argument("--budget-usd", type=float,
                        help="stop dispatching new requests once cumulative estimated "
                             "cost reaches this many dollars")
    parser.add_argument("--concurrency", type=int, default=24,
                        help="concurrent OpenRouter requests in flight (default: 24)")
    parser.add_argument("--doc-concurrency", type=int, default=12,
                        help="documents being worked on at once (default: 12)")
    parser.add_argument("--render-workers", type=int, default=8,
                        help="thread pool size for PyMuPDF page rendering (default: 8)")
    parser.add_argument("--batch-size", type=int, default=6,
                        help="pages sent concurrently per document before checking the "
                             "not-Badini skip heuristic (default: 6)")
    parser.add_argument("--skip-after", type=int, default=5,
                        help="skip the rest of a document after this many consecutive "
                             "non-Badini pages (0 disables)")
    parser.add_argument("--ignore-doc-skips", action="store_true",
                        help="re-attempt documents previously skipped as non-Badini or errored")
    parser.add_argument("--no-shuffle", action="store_true",
                        help="process documents in directory order instead of a fixed "
                             "shuffle (shuffling spreads a --budget-usd cap across sources)")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
