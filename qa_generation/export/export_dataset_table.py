#!/usr/bin/env python3
"""Flatten the delivered dataset to a table: CSV and Parquet.

This reads **output/dataset/qa_pairs.jsonl itself**, not the generation
records, and that is the whole point. `export_qa_csv.py` builds a *review*
sheet from the raw generations and is deliberately a different shape --
it has extra QC columns, it includes pairs whose chunk text went missing,
and it has no `context_mode`, because the with/without-context split is
assigned at compile time. Using it as "the dataset in CSV form" would be
wrong on all three counts: it carries 952,822 rows against the deliverable's
952,801, and it does not show the user message that is actually trained on.

Flattening the deliverable instead makes the correspondence exact and
checkable: same rows, same order, same count, nothing derived. `--verify`
reconstructs records from the written table and diffs them against the
source JSONL.

Columns are the JSONL content and nothing else:

    system, user, assistant, reasoning   <- the three messages
    document_id, chunk_id, source, question_type, context_mode   <- metadata

`user` is kept whole ("Context: ...\\n\\nQuestion: ..." or just
"Question: ...") rather than split into separate question/context columns.
Splitting is easy to want, but context is ~90% of the bytes, so carrying it
twice would nearly double a multi-GB file -- and the joined string is what
the model is actually trained on, so it is the honest column.

Parquet is written sharded, in HuggingFace's `train-00000-of-000NN`
convention. Prefer it over the CSV for the Hub: the dataset viewer reads
Parquet natively, it is typed, and it is several times smaller. The CSV is
for spreadsheets and tools that insist on it.

Run inside the conda "ai" env, from the repo root:
    python3 qa_generation/export_dataset_table.py
    python3 qa_generation/export_dataset_table.py --verify
    python3 qa_generation/export_dataset_table.py --no-parquet
"""

import argparse
import csv
import json
import sys
from pathlib import Path
import time

# qa_config and gemma_tokenizer live one level up, in qa_generation/, and
# are shared by every stage. Adding the parent explicitly keeps these
# runnable as plain scripts from anywhere -- `python3 qa_generation/export/
# export_outliers.py` -- rather than only from their own directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qa_config as cfg

COLUMNS = [
    "system", "user", "assistant", "reasoning",
    "document_id", "chunk_id", "source", "question_type", "context_mode",
]

# CSV cannot distinguish "absent" from "empty string", so a record with no
# reasoning round-trips as "". Both mean the same thing here: the pair did
# not need a thought channel.
#
# Small on purpose. Rows carry the full user message (~2.5KB with context),
# so a batch is ~50MB of live objects; buffering a whole 250k-row shard
# before writing would be ~625MB and would not fit next to everything else
# on this machine. Each batch is written as one Parquet row group through a
# streaming ParquetWriter, so only one batch is ever resident.
BATCH_ROWS = 20_000


def flatten(record: dict) -> dict:
    messages = {m["role"]: m for m in record["messages"]}
    assistant = messages["assistant"]
    meta = record["metadata"]
    return {
        "system": messages["system"]["content"],
        "user": messages["user"]["content"],
        "assistant": assistant["content"],
        "reasoning": assistant.get("reasoning") or "",
        "document_id": meta["document_id"],
        "chunk_id": meta["chunk_id"],
        "source": meta["source"],
        "question_type": meta["question_type"],
        "context_mode": meta["context_mode"],
    }


def unflatten(row: dict) -> dict:
    """Inverse of flatten(), used only by --verify."""
    assistant = {"role": "assistant", "content": row["assistant"]}
    if row.get("reasoning"):
        assistant["reasoning"] = row["reasoning"]
    return {
        "messages": [
            {"role": "system", "content": row["system"]},
            {"role": "user", "content": row["user"]},
            assistant,
        ],
        "metadata": {
            "document_id": row["document_id"],
            "chunk_id": row["chunk_id"],
            "source": row["source"],
            "question_type": row["question_type"],
            "context_mode": row["context_mode"],
        },
    }


def iter_records(path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def iter_table_rows(parquet_dir, csv_path):
    """Stream the written table back, shard by shard.

    Streams for the same reason everything else here does: concatenating the
    shards into one DataFrame would materialise the whole ~2.4GB dataset,
    which is precisely what this file exists to avoid."""
    shards = sorted(parquet_dir.glob("train-*.parquet"))
    if shards:
        import pyarrow.parquet as pq
        for shard in shards:
            for batch in pq.ParquetFile(shard).iter_batches(batch_size=BATCH_ROWS):
                for row in batch.to_pylist():
                    yield {k: (v if v is not None else "") for k, v in row.items()}
        return
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                yield row


def verify(jsonl_path, parquet_dir, csv_path, sample_every: int) -> int:
    """Reconstruct from the written table and diff against the source."""
    print(f"Verifying every {sample_every}th record round-trips ...")
    checked = mismatched = 0
    n_jsonl = n_table = 0

    records = iter_records(jsonl_path)
    rows = iter_table_rows(parquet_dir, csv_path)
    for i, record in enumerate(records):
        n_jsonl += 1
        row = next(rows, None)
        if row is None:
            print(f"  FAIL: table ran out at record {i:,}; JSONL has more")
            return 1
        n_table += 1
        if i % sample_every:
            continue
        rebuilt = unflatten(row)
        if rebuilt != record:
            mismatched += 1
            if mismatched == 1:
                print(f"  first mismatch at record {i}:")
                print(f"    jsonl : {json.dumps(record, ensure_ascii=False)[:220]}")
                print(f"    table : {json.dumps(rebuilt, ensure_ascii=False)[:220]}")
        checked += 1

    extra = sum(1 for _ in rows)
    n_table += extra
    ok = mismatched == 0 and extra == 0
    print(f"  rows: jsonl {n_jsonl:,} vs table {n_table:,} "
          f"-> {'MATCH' if n_jsonl == n_table else 'MISMATCH'}")
    print(f"  records checked: {checked:,}   mismatches: {mismatched}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows-per-shard", type=int, default=250_000,
                        help="rows per Parquet shard (default: 250000, giving "
                             "Hub-friendly shards well under 500MB)")
    parser.add_argument("--no-parquet", action="store_true")
    parser.add_argument("--no-csv", action="store_true")
    parser.add_argument("--verify", action="store_true",
                        help="after writing, reconstruct records from the table "
                             "and diff them against qa_pairs.jsonl")
    parser.add_argument("--verify-every", type=int, default=500,
                        help="verify every Nth record (default: 500)")
    args = parser.parse_args()

    src = cfg.DATASET_DIR / "qa_pairs.jsonl"
    if not src.is_file():
        sys.exit(f"{src} does not exist; run compile_qa_dataset.py first.")

    csv_path = cfg.DATASET_DIR / "qa_pairs.csv"
    parquet_dir = cfg.DATASET_DIR / "parquet"

    writer_pq = None
    schema = None
    if not args.no_parquet:
        import pyarrow as pa
        import pyarrow.parquet as pq
        parquet_dir.mkdir(parents=True, exist_ok=True)
        for stale in parquet_dir.glob("train-*.parquet"):
            stale.unlink()
        schema = pa.schema([(name, pa.string()) for name in COLUMNS])

    csv_handle = csv_writer = None
    if not args.no_csv:
        csv_handle = csv_path.open("w", encoding="utf-8", newline="")
        csv_writer = csv.DictWriter(csv_handle, fieldnames=COLUMNS,
                                    quoting=csv.QUOTE_ALL)
        csv_writer.writeheader()

    print(f"Reading {src} ...")
    started = time.time()
    batch = []
    shard_index = 0
    shard_row_count = 0
    written = 0
    shard_paths = []

    def flush_batch():
        nonlocal batch, writer_pq, shard_index, shard_row_count
        if not batch:
            return
        if csv_writer is not None:
            csv_writer.writerows(batch)
        if schema is not None:
            import pyarrow as pa
            import pyarrow.parquet as pq
            if writer_pq is None:
                path = parquet_dir / f"train-{shard_index:05d}.parquet"
                writer_pq = pq.ParquetWriter(path, schema, compression="zstd")
                shard_paths.append(path)
            writer_pq.write_table(pa.Table.from_pydict(
                {name: [row[name] for row in batch] for name in COLUMNS},
                schema=schema))
            shard_row_count += len(batch)
            if shard_row_count >= args.rows_per_shard:
                writer_pq.close()
                writer_pq = None
                shard_index += 1
                shard_row_count = 0
        batch = []

    for record in iter_records(src):
        batch.append(flatten(record))
        written += 1
        if len(batch) >= BATCH_ROWS:
            flush_batch()
            if written % 200_000 == 0:
                print(f"  {written:,} rows ({time.time() - started:.0f}s)")
    flush_batch()
    if writer_pq is not None:
        writer_pq.close()
        writer_pq = None

    if csv_handle is not None:
        csv_handle.close()

    print(f"\n{written:,} rows in {time.time() - started:.0f}s")
    if csv_writer is not None:
        print(f"  CSV     : {csv_path} ({csv_path.stat().st_size / 1e9:.2f} GB)")
    if shard_paths:
        total = sum(p.stat().st_size for p in shard_paths)
        # Rename to the final of-N form now that N is known.
        renamed = []
        for i, path in enumerate(sorted(shard_paths)):
            final = parquet_dir / f"train-{i:05d}-of-{len(shard_paths):05d}.parquet"
            path.rename(final)
            renamed.append(final)
        print(f"  Parquet : {parquet_dir}/ "
              f"({len(renamed)} shards, {total / 1e9:.2f} GB total)")
        for path in renamed:
            print(f"              {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")

    if args.verify:
        print()
        return verify(src, parquet_dir, csv_path, args.verify_every)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
