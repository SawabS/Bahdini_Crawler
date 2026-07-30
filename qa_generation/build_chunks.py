#!/usr/bin/env python3
"""Build the QA-generation work queue: clean Bahdini text -> context chunks.

Reads two source pools:

  extractions/<source>/safe/*.txt             native PDF text extraction,
                                               already classified "safe" by
                                               scripts/extract_pipeline.py
                                               (no review gate)
  gemini_ocr_pipeline/output/corpus_unreviewed/  Gemini-OCR'd text; only rows
                                               with classification=="kurdish"
                                               AND review_status=="reviewed"
                                               are included by default, since
                                               that pipeline explicitly does
                                               NOT promote OCR output to
                                               training data automatically

Each document is split into paragraph-aware chunks sized for the ~1,000
token/QA-record budget the partner side estimated (see qa_config.py), and
every chunk is written as one row to qa_generation/output/chunks.jsonl -- the
work queue generate_qa_openrouter.py consumes.

Run inside the conda "ai" env (no extra deps beyond stdlib are required):
    python3 qa_generation/build_chunks.py
    python3 qa_generation/build_chunks.py --include-unreviewed-ocr
"""

import argparse
import json
import re
import sys
from collections import Counter

import gemma_tokenizer as gtok
import qa_config as cfg

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?؟۔।])\s+")


def split_paragraphs(text: str) -> list:
    paragraphs = []
    for page in text.split("\f"):
        for para in re.split(r"\n\s*\n", page):
            para = para.strip("\n").strip()
            if para:
                paragraphs.append(para)
    return paragraphs


def token_hard_cut(text: str, max_tokens: int) -> list:
    """Last-resort cut for a "sentence" with no punctuation to split on.
    Cuts on real token boundaries (encode -> slice ids -> decode) when the
    tokenizer is available, so pieces are guaranteed <= max_tokens instead of
    just approximately so; falls back to a character slice otherwise."""
    ids = gtok.encode(text)
    if ids is None:
        max_chars = int(max_tokens * cfg.CHARS_PER_TOKEN)
        return [text[start:start + max_chars] for start in range(0, len(text), max_chars)]
    return [gtok.decode(ids[start:start + max_tokens]) for start in range(0, len(ids), max_tokens)]


def hard_split(paragraph: str, max_tokens: int) -> list:
    """Split one oversized paragraph on sentence boundaries, falling back to
    a hard token/character cut if a single "sentence" is still too long."""
    sentences = [s for s in SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
    if not sentences:
        sentences = [paragraph]
    sentence_tokens_list = gtok.count_tokens_batch(sentences)

    pieces = []
    current = []
    current_tokens = 0
    for sentence, sentence_tokens in zip(sentences, sentence_tokens_list):
        if sentence_tokens > max_tokens:
            if current:
                pieces.append(" ".join(current))
                current, current_tokens = [], 0
            pieces.extend(token_hard_cut(sentence, max_tokens))
            continue
        if current and current_tokens + sentence_tokens > max_tokens:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(sentence)
        current_tokens += sentence_tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_text(text: str, target_tokens: int, max_tokens: int, min_tokens: int) -> list:
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []
    para_tokens_list = gtok.count_tokens_batch(paragraphs)

    chunks = []
    current = []
    current_tokens = 0

    def flush():
        if current:
            chunks.append("\n\n".join(current))

    for para, para_tokens in zip(paragraphs, para_tokens_list):
        if para_tokens > max_tokens:
            flush()
            current, current_tokens = [], 0
            chunks.extend(hard_split(para, max_tokens))
            continue
        if current and current_tokens + para_tokens > target_tokens:
            flush()
            current, current_tokens = [], 0
        current.append(para)
        current_tokens += para_tokens
    flush()

    # merge a too-thin trailing fragment into its predecessor rather than
    # shipping a chunk that can't ground a QA pair on its own
    chunk_tokens_list = gtok.count_tokens_batch(chunks)
    merged = []
    merged_tokens = []
    for chunk, chunk_tokens in zip(chunks, chunk_tokens_list):
        if merged and chunk_tokens < min_tokens:
            merged[-1] = merged[-1] + "\n\n" + chunk
            merged_tokens[-1] = gtok.count_tokens(merged[-1])
        else:
            merged.append(chunk)
            merged_tokens.append(chunk_tokens)
    return [c for c, t in zip(merged, merged_tokens) if t >= min_tokens]


def discover_safe_docs() -> list:
    docs = []
    for manifest_path in sorted(cfg.EXTRACTIONS_DIR.glob("*/safe/_manifest.jsonl")):
        source = manifest_path.parent.parent.name
        with open(manifest_path, encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                out = row.get("out")
                if not out:
                    continue
                text_path = cfg.ROOT / out
                if not text_path.is_file():
                    continue
                docs.append({
                    "source": source,
                    "origin": "safe_extraction",
                    "doc_file": row.get("input", row.get("file")),
                    "document_id": cfg.doc_id(source, row.get("input", row.get("file", out))),
                    "text_path": text_path,
                })
    return docs


def discover_ocr_docs(include_unreviewed: bool) -> list:
    if not cfg.OCR_CORPUS_JSONL.is_file():
        return []
    docs = []
    skipped_unreviewed = 0
    with open(cfg.OCR_CORPUS_JSONL, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("classification") != "kurdish" or not row.get("out"):
                continue
            reviewed = row.get("review_status") == "reviewed"
            if not reviewed:
                if not include_unreviewed:
                    skipped_unreviewed += 1
                    continue
                if row.get("completeness", 0) < cfg.OCR_MIN_COMPLETENESS_IF_UNREVIEWED:
                    continue
            text_path = cfg.ROOT / row["out"]
            if not text_path.is_file():
                continue
            docs.append({
                "source": row["source"],
                "origin": "ocr_reviewed" if reviewed else "ocr_unreviewed",
                "doc_file": row["file"],
                "document_id": row["doc_id"],
                "text_path": text_path,
            })
    if skipped_unreviewed and not include_unreviewed:
        print(f"  (skipped {skipped_unreviewed} unreviewed OCR documents classified "
              f"'kurdish'; pass --include-unreviewed-ocr to pull from them anyway)",
              file=sys.stderr)
    return docs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--include-unreviewed-ocr", action="store_true",
                        help="also chunk gemini_ocr_pipeline OCR output that has not been "
                             "human-reviewed yet, restricted to classification=kurdish and "
                             f"completeness >= {cfg.OCR_MIN_COMPLETENESS_IF_UNREVIEWED}. "
                             "This bypasses the OCR pipeline's review gate -- only use it "
                             "for an early/throwaway sample, not the delivered dataset.")
    parser.add_argument("--target-tokens", type=int, default=cfg.TARGET_CHUNK_TOKENS)
    parser.add_argument("--max-tokens", type=int, default=cfg.MAX_CHUNK_TOKENS)
    parser.add_argument("--min-tokens", type=int, default=cfg.MIN_CHUNK_TOKENS)
    args = parser.parse_args()

    if args.include_unreviewed_ocr:
        print(">>> --include-unreviewed-ocr set: pulling unreviewed Gemini-OCR text into "
              "the chunk pool. Nothing OCR'd has been human-reviewed yet -- treat any "
              "dataset built from this run as a throwaway sample, not the deliverable.\n",
              file=sys.stderr)

    docs = discover_safe_docs() + discover_ocr_docs(args.include_unreviewed_ocr)
    if not docs:
        sys.exit("No source documents found under extractions/*/safe/ or "
                  f"{cfg.OCR_CORPUS_DIR}; nothing to chunk.")

    if gtok.available():
        print(f"Using the real {cfg.GEMMA_TOKENIZER_MODEL} tokenizer for token counts.")
    else:
        print(f"Real tokenizer unavailable; falling back to the "
              f"{cfg.CHARS_PER_TOKEN} chars/token estimate.")

    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    by_source = Counter()
    by_origin = Counter()
    total_chunks = 0
    total_tokens = 0
    skipped_empty = 0

    with open(cfg.CHUNKS_PATH, "w", encoding="utf-8") as out_handle:
        for doc in docs:
            text = doc["text_path"].read_text(encoding="utf-8", errors="ignore")
            chunks = chunk_text(text, args.target_tokens, args.max_tokens, args.min_tokens)
            if not chunks:
                skipped_empty += 1
                continue
            for i, (chunk, token_estimate) in enumerate(
                    zip(chunks, gtok.count_tokens_batch(chunks))):
                record = {
                    "chunk_id": f"{doc['document_id']}-{i:03d}",
                    "document_id": doc["document_id"],
                    "source": doc["source"],
                    "origin": doc["origin"],
                    "doc_file": doc["doc_file"],
                    "chunk_index": i,
                    "n_chunks": len(chunks),
                    "char_count": len(chunk),
                    "token_estimate": token_estimate,
                    "text": chunk,
                }
                out_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                total_chunks += 1
                total_tokens += token_estimate
                by_source[doc["source"]] += 1
                by_origin[doc["origin"]] += 1

    lines = ["# QA chunk queue", ""]
    lines.append(f"Documents seen: {len(docs)}  |  documents skipped (no usable chunks): "
                 f"{skipped_empty}  |  chunks written: {total_chunks}  |  "
                 f"est. total context tokens: {total_tokens:,}")
    lines += ["", "| source | chunks |", "|---|---|"]
    for source in sorted(by_source):
        lines.append(f"| {source} | {by_source[source]} |")
    lines += ["", "| origin | chunks |", "|---|---|"]
    for origin in sorted(by_origin):
        lines.append(f"| {origin} | {by_origin[origin]} |")
    cfg.CHUNKS_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {total_chunks} chunks from {len(docs)} documents to {cfg.CHUNKS_PATH}")
    print(f"Estimated total context tokens: {total_tokens:,}")
    print(f"Report: {cfg.CHUNKS_REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
