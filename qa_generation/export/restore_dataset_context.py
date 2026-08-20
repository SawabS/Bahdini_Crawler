#!/usr/bin/env python3
"""Restore source context to every delivered QA record.

The canonical dataset deliberately omits the source chunk from records whose
metadata.context_mode is ``no_context``.  Generation still retained a chunk_id,
so those records can be restored without regenerating any question or answer.

This utility preserves the canonical files and writes a new, all-context JSONL
plus an exactly flattened CSV.  Outputs are written through temporary files and
renamed only after the complete stream succeeds.
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qa_config as cfg
from export_dataset_table import COLUMNS, flatten
from pipeline.compile_qa_dataset import build_chunk_offset_index


DEFAULT_INPUT = cfg.DATASET_DIR / "qa_pairs.jsonl"
DEFAULT_JSONL_OUTPUT = cfg.DATASET_DIR / "qa_pairs_with_context.jsonl"
DEFAULT_CSV_OUTPUT = cfg.DATASET_DIR / "qa_pairs_with_context.csv"


def role_message(record: dict, role: str, line_number: int) -> dict:
    matches = [message for message in record.get("messages", [])
               if message.get("role") == role]
    if len(matches) != 1:
        raise ValueError(
            f"line {line_number:,}: expected exactly one {role!r} message, "
            f"found {len(matches)}")
    return matches[0]


def bare_question(user_content: str, line_number: int) -> str:
    prefix = "Question:"
    if not user_content.startswith(prefix):
        raise ValueError(
            f"line {line_number:,}: no_context user message does not start "
            f"with {prefix!r}")
    question = user_content[len(prefix):].lstrip()
    if not question:
        raise ValueError(f"line {line_number:,}: empty question")
    return question


def restore_record(record: dict, chunk_text: Optional[str], line_number: int) -> bool:
    """Restore one record in place; return True when context was added."""
    metadata = record.get("metadata") or {}
    mode = metadata.get("context_mode")
    system = role_message(record, "system", line_number)
    user = role_message(record, "user", line_number)
    role_message(record, "assistant", line_number)

    if mode == "with_context":
        if not user.get("content", "").startswith("Context: "):
            raise ValueError(
                f"line {line_number:,}: with_context record lacks a Context block")
        return False
    if mode != "no_context":
        raise ValueError(
            f"line {line_number:,}: unsupported context_mode {mode!r}")
    if not chunk_text:
        raise ValueError(
            f"line {line_number:,}: source chunk is missing or empty for "
            f"{metadata.get('chunk_id')!r}")

    question = bare_question(user.get("content", ""), line_number)
    system["content"] = cfg.QA_SYSTEM_PROMPT
    user["content"] = f"Context: {chunk_text}\n\nQuestion: {question}"
    metadata["context_mode"] = "with_context"
    return True


def temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.tmp")


def require_writable_outputs(paths: list[Path], overwrite: bool) -> None:
    for path in paths:
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"{path} already exists; pass --overwrite to replace it")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = temporary_path(path)
        if tmp.exists():
            tmp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help=f"source JSONL (default: {DEFAULT_INPUT})")
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_JSONL_OUTPUT,
                        help=f"restored JSONL (default: {DEFAULT_JSONL_OUTPUT})")
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_CSV_OUTPUT,
                        help=f"restored CSV (default: {DEFAULT_CSV_OUTPUT})")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace existing restored outputs")
    parser.add_argument("--progress-every", type=int, default=100_000,
                        help="progress interval in records (default: 100000)")
    args = parser.parse_args()

    input_path = args.input.resolve()
    jsonl_path = args.output_jsonl.resolve()
    csv_path = args.output_csv.resolve()
    if not input_path.is_file():
        parser.error(f"input does not exist: {input_path}")
    if input_path in {jsonl_path, csv_path}:
        parser.error("outputs must not overwrite the input dataset")

    outputs = [jsonl_path, csv_path]
    require_writable_outputs(outputs, args.overwrite)
    jsonl_tmp = temporary_path(jsonl_path)
    csv_tmp = temporary_path(csv_path)

    print("Indexing source chunks by byte offset ...")
    offsets = build_chunk_offset_index()
    print(f"  {len(offsets):,} chunks indexed")

    total = restored = already_with_context = 0
    started = time.time()
    current_chunk_id = None
    current_chunk_text = None

    try:
        with input_path.open(encoding="utf-8") as input_handle, \
                cfg.CHUNKS_PATH.open(encoding="utf-8") as chunks_handle, \
                jsonl_tmp.open("w", encoding="utf-8") as jsonl_handle, \
                csv_tmp.open("w", encoding="utf-8", newline="") as csv_handle:
            csv_writer = csv.DictWriter(
                csv_handle, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
            csv_writer.writeheader()

            for line_number, line in enumerate(input_handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"line {line_number:,}: invalid JSON: {error}") from error

                mode = (record.get("metadata") or {}).get("context_mode")
                if mode == "no_context":
                    chunk_id = record["metadata"].get("chunk_id")
                    if chunk_id != current_chunk_id:
                        current_chunk_id = chunk_id
                        offset = offsets.get(chunk_id)
                        if offset is None:
                            current_chunk_text = None
                        else:
                            chunks_handle.seek(offset)
                            chunk = json.loads(chunks_handle.readline())
                            if chunk.get("chunk_id") != chunk_id:
                                raise ValueError(
                                    f"line {line_number:,}: chunk index resolved "
                                    f"{chunk_id!r} to {chunk.get('chunk_id')!r}")
                            current_chunk_text = chunk.get("text")

                changed = restore_record(
                    record,
                    current_chunk_text if mode == "no_context" else None,
                    line_number,
                )
                restored += int(changed)
                already_with_context += int(not changed)
                total += 1

                jsonl_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                csv_writer.writerow(flatten(record))

                if args.progress_every > 0 and total % args.progress_every == 0:
                    print(
                        f"  {total:,} records; {restored:,} restored "
                        f"({time.time() - started:.0f}s)")

        os.replace(jsonl_tmp, jsonl_path)
        os.replace(csv_tmp, csv_path)
    except BaseException:
        jsonl_tmp.unlink(missing_ok=True)
        csv_tmp.unlink(missing_ok=True)
        raise

    print(f"\nWrote {total:,} records in {time.time() - started:.0f}s")
    print(f"  restored from no_context : {restored:,}")
    print(f"  already with context     : {already_with_context:,}")
    print(f"  JSONL                    : {jsonl_path} "
          f"({jsonl_path.stat().st_size / 1e9:.2f} GB)")
    print(f"  CSV                      : {csv_path} "
          f"({csv_path.stat().st_size / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
