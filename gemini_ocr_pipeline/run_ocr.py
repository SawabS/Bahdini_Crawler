#!/usr/bin/env python3
"""Resumable Gemini OCR runner for the scanned-PDF queue.

For every queued document (see build_manifest.py) each page is rendered as a
high-resolution grayscale PNG and sent individually to Gemini through Vertex
AI, so a malformed legacy PDF text layer can never bias the transcription.
One JSONL record per page is appended to
gemini_ocr_pipeline/output/pages/<source>/<doc_id>.jsonl with the exact model,
prompt version, image hash, token usage, and cost estimate.

Re-running skips pages that already have a successful record, so the runner
can be interrupted at any time. Pages whose last attempt failed are retried.

Nothing produced here is training data yet: compile_corpus.py assembles the
page records into reviewable corpus files.

Examples (inside the conda "ai" env):
    # small pilot: 10 pages total across the zcks source
    conda run --no-capture-output -n ai python -u gemini_ocr_pipeline/run_ocr.py \
      --source zcks --max-pages 10

    # full queue, 4 concurrent Gemini requests
    conda run --no-capture-output -n ai python -u gemini_ocr_pipeline/run_ocr.py --workers 4
"""

import argparse
import hashlib
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import fitz

import ocr_config as cfg

RETRY_ATTEMPTS = 5
# pixmaps whose gray values span less than this are treated as blank pages
BLANK_SPAN = 8


def load_queue(args) -> list:
    if not cfg.MANIFEST_PATH.is_file():
        sys.exit(f"No queue at {cfg.MANIFEST_PATH}; run build_manifest.py first.")
    queue = []
    with open(cfg.MANIFEST_PATH, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if args.source and row["source"] not in args.source:
                continue
            if args.doc and args.doc not in row["input"]:
                continue
            queue.append(row)
    if args.max_docs:
        queue = queue[: args.max_docs]
    return queue


DONE_STATUSES = ("ok", "no_text", "blank", "not_badini")


def page_statuses(record_path) -> dict:
    """Latest status per page; page 0 holds a document-level skip marker."""
    statuses = {}
    if record_path.is_file():
        with open(record_path, encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                statuses[record["page"]] = record["status"]
    return statuses


def render_page(document, page_index: int):
    """Render one page to grayscale PNG bytes, capped at MAX_LONG_SIDE_PX."""
    page = document[page_index]
    long_side = max(page.rect.width, page.rect.height)
    zoom = min(cfg.RENDER_ZOOM, cfg.MAX_LONG_SIDE_PX / long_side if long_side else cfg.RENDER_ZOOM)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    blank = not pix.samples or (max(pix.samples) - min(pix.samples)) < BLANK_SPAN
    return pix.tobytes("png"), (pix.width, pix.height), blank


def call_gemini(client, png_bytes: bytes):
    """One transcription request with retries; returns the result fields."""
    from google.genai import types

    last_error = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=cfg.GEMINI_MODEL,
                contents=[
                    cfg.PROMPT,
                    types.Part.from_bytes(
                        data=png_bytes,
                        mime_type="image/png",
                        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
                    ),
                ],
                config=types.GenerateContentConfig(
                    temperature=0, max_output_tokens=cfg.MAX_OUTPUT_TOKENS
                ),
            )
        except Exception as error:  # quota, network, transient server errors
            last_error = error
            time.sleep(2 * 2 ** attempt + random.uniform(0, 1))
            continue
        text = (response.text or "").strip()
        usage = response.usage_metadata
        input_tokens = (usage.prompt_token_count or 0) if usage else 0
        output_tokens = (usage.candidates_token_count or 0) if usage else 0
        finish_reason = None
        if response.candidates:
            reason = response.candidates[0].finish_reason
            finish_reason = reason.name if reason else None
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
            "est_cost_usd": round(cfg.estimate_cost_usd(input_tokens, output_tokens), 6),
        }
    return {"status": "error", "text": "", "error": f"{type(last_error).__name__}: {last_error}"}


def consecutive_not_badini(statuses: dict) -> int:
    """Longest run of page-adjacent not_badini results; blank pages are neutral."""
    longest = run = 0
    for page in sorted(page for page in statuses if page > 0):
        status = statuses[page]
        if status == "not_badini":
            run += 1
            longest = max(longest, run)
        elif status not in ("blank", "no_text"):
            run = 0
    return longest


def process_document(client, row, budget: int, args, totals) -> int:
    """OCR all pending pages of one document; returns pages attempted."""
    source_path = cfg.ROOT / row["path"]
    record_path = cfg.PAGES_DIR / row["source"] / f"{row['doc_id']}.jsonl"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    statuses = page_statuses(record_path)
    if statuses.get(0) == "doc_skipped" and not args.ignore_doc_skips:
        return 0
    done = {page for page, status in statuses.items() if status in DONE_STATUSES}

    base = {
        "source": row["source"],
        "file": row["input"],
        "doc_id": row["doc_id"],
        "model": cfg.GEMINI_MODEL,
        "prompt_version": cfg.PROMPT_VERSION,
    }

    def finish(page_number, size, png_bytes, result):
        record = dict(base)
        record.update(
            page=page_number,
            image_sha256=hashlib.sha256(png_bytes).hexdigest(),
            image_px=list(size),
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **result,
        )
        with open(record_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        statuses[page_number] = record["status"]
        totals[record["status"]] = totals.get(record["status"], 0) + 1
        totals["input_tokens"] += record.get("input_tokens", 0)
        totals["output_tokens"] += record.get("output_tokens", 0)
        totals["est_cost_usd"] += record.get("est_cost_usd", 0.0)
        print(f"  {row['source']}/{row['input']} p{page_number}: {record['status']}")

    # Pages are rendered and sent in small batches so a long book never holds
    # more than a few rendered PNGs in memory at once.
    batch_size = max(args.workers * 3, 3)
    with fitz.open(source_path) as document:
        base["n_pages"] = len(document)
        pending = [page for page in range(1, len(document) + 1) if page not in done]
        if budget >= 0:
            pending = pending[:budget]
        if not pending:
            return 0

        for start in range(0, len(pending), batch_size):
            api_pages = []
            for page_number in pending[start:start + batch_size]:
                png_bytes, size, blank = render_page(document, page_number - 1)
                if args.keep_images:
                    image_dir = cfg.OUTPUT_DIR / "images" / row["doc_id"]
                    image_dir.mkdir(parents=True, exist_ok=True)
                    (image_dir / f"page_{page_number:04d}.png").write_bytes(png_bytes)
                if blank:
                    finish(page_number, size, png_bytes, {"status": "blank", "text": ""})
                elif args.dry_run:
                    finish(page_number, size, png_bytes, {"status": "dry_run", "text": ""})
                else:
                    api_pages.append((page_number, png_bytes, size))

            if api_pages:
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    futures = {
                        pool.submit(call_gemini, client, png_bytes): (page_number, size, png_bytes)
                        for page_number, png_bytes, size in api_pages
                    }
                    for future in as_completed(futures):
                        page_number, size, png_bytes = futures[future]
                        finish(page_number, size, png_bytes, future.result())

            # a run of consecutive non-Badini pages means the whole book is in
            # another language: mark it skipped instead of paying for the rest
            if (args.skip_after
                    and start + batch_size < len(pending)
                    and consecutive_not_badini(statuses) >= args.skip_after):
                skip_record = dict(base)
                skip_record.update(
                    page=0, status="doc_skipped",
                    reason=f"{args.skip_after} consecutive not_badini pages",
                    ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
                with open(record_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(skip_record, ensure_ascii=False) + "\n")
                totals["docs_skipped"] = totals.get("docs_skipped", 0) + 1
                print(f"  {row['source']}/{row['input']}: skipped rest of document "
                      f"(not Badini)")
                return min(start + batch_size, len(pending))
    return len(pending)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", action="append", help="limit to one source (repeatable)")
    parser.add_argument("--doc", help="only documents whose input path contains this substring")
    parser.add_argument("--max-docs", type=int, help="stop after this many documents")
    parser.add_argument("--max-pages", type=int,
                        help="global budget of pages to attempt this run (for pilots)")
    parser.add_argument("--workers", type=int, default=2, help="concurrent Gemini requests")
    parser.add_argument("--keep-images", action="store_true",
                        help="keep rendered PNGs under output/images/<doc_id>/")
    parser.add_argument("--skip-after", type=int, default=5,
                        help="skip the rest of a document after this many consecutive "
                             "non-Badini pages (0 disables)")
    parser.add_argument("--ignore-doc-skips", action="store_true",
                        help="re-attempt documents previously skipped as non-Badini")
    parser.add_argument("--dry-run", action="store_true",
                        help="render and record pages without calling Gemini")
    args = parser.parse_args()

    queue = load_queue(args)
    if not queue:
        print("Nothing matches the given filters.")
        return 0

    client = None
    if not args.dry_run:
        from google import genai

        client = genai.Client(vertexai=True, project=cfg.PROJECT, location=cfg.VERTEX_LOCATION)

    totals = {"input_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0}
    remaining = args.max_pages if args.max_pages else -1
    try:
        for row in queue:
            if remaining == 0:
                break
            attempted = process_document(client, row, remaining, args, totals)
            if remaining > 0:
                remaining -= attempted
    except KeyboardInterrupt:
        print("\nInterrupted; every finished page is already saved. Re-run to resume.")

    print("\nRun summary:")
    for key, value in sorted(totals.items()):
        if key == "est_cost_usd":
            print(f"  {key}: ${value:.4f}")
        else:
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
