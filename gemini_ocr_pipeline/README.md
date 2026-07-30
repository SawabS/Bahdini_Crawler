# Gemini OCR pipeline

> Operational quick reference. The full A–Z contract — cloud setup, review
> protocol, troubleshooting, checklists — is
> [docs/DOCUMENT_AI_OCR_GUIDE.md](../docs/DOCUMENT_AI_OCR_GUIDE.md), the
> single source of truth for OCR in this repository.

Turns the scanned-PDF OCR queue (`extractions/needs_ocr` manifests) into a
Bahdini text corpus using `gemini-3.1-flash-lite` through Vertex AI
(project `bahdini-data`). Chosen over Document AI Enterprise OCR after an
A/B pilot: fewer Kurdish-character errors and roughly 40% cheaper.

Every page is rendered as a high-resolution grayscale PNG and sent as an
image, never as a PDF, so malformed legacy text layers cannot bias the
transcription.

## Stages

```text
extractions/*/_manifest.jsonl  (status=needs_ocr)
        |  build_manifest.py
        v
output/manifest.jsonl                      work queue, one row per PDF
        |  run_ocr.py                      resumable; page -> PNG -> Gemini
        v
output/pages/<source>/<doc_id>.jsonl       one record per page attempt
        |  compile_corpus.py
        v
output/corpus/                             per-document .txt + corpus.jsonl
                                           + report.md + pre-train candidate
```

All commands run inside the conda `ai` environment:

```bash
conda run --no-capture-output -n ai python gemini_ocr_pipeline/build_manifest.py
conda run --no-capture-output -n ai python -u gemini_ocr_pipeline/run_ocr.py --workers 4
conda run --no-capture-output -n ai python gemini_ocr_pipeline/compile_corpus.py
```

## The work queue

`build_manifest.py` re-reads the extraction manifests and keeps only
documents still present on disk, so it can be re-run as downloads complete.
Current queue: ~2,869 documents / ~201,327 pages across facebook,
pertokenbadini, spirez, telegram_*, zcks.

## The runner

`run_ocr.py` is safe to interrupt at any time; every finished page is already
on disk and a re-run resumes exactly where it stopped. Useful flags:

- `--source SRC` / `--doc SUBSTRING` / `--max-docs N` — select work
- `--max-pages N` — global page budget, for pilots
- `--workers N` — concurrent Gemini requests (default 2)
- `--skip-after N` — language gate: after N consecutive `[NOT_BADINI]` pages
  the rest of the document is skipped (default 5, 0 disables); blank and
  `[NO_TEXT]` pages are neutral. Re-attempt skipped docs with
  `--ignore-doc-skips`.
- `--dry-run` — render and record without calling Gemini
- `--keep-images` — keep rendered PNGs under `output/images/<doc_id>/`

The prompt (see `ocr_config.py`, versioned, currently `v4`) instructs Gemini
to: judge the page language first and answer `[NOT_BADINI: <language>]`
instead of transcribing non-Badini pages; transcribe exactly with no
translation or normalization; skip page numbers and running headers; be
precise about confusable letters (the `v4` dot-count rule measurably fixed
ڤ/ق confusions); mark unreadable words `[unclear]`; answer `[NO_TEXT]` for
pages with no body text.

Each page record stores model, prompt version, image SHA-256, pixel size,
finish reason, token usage, estimated cost, and the raw transcription, so any
page can be audited or selectively re-run later.

## The compiler

`compile_corpus.py` assembles page records into:

- `corpus/<source>/<document>.txt`, pages joined with `\n\f\n`,
  normalized like the native-extraction corpus (NFKC + KLPT; disable with
  `--no-normalize`)
- `corpus/corpus.jsonl`, per-document stats: completeness,
  Kurdish-letter ratio, `[unclear]` count, cost, classification
  (`kurdish` / `not_badini` / `arabic_not_kurdish` / `low_text` /
  `not_arabic_script`)
- `corpus/report.md`, per-source totals and a review-first list
- `corpus/pretrain_candidate.txt`, complete, Kurdish-classified documents
  concatenated for pre-training

This corpus has been reviewed and is accepted for use. The classification
fields and the review-first list in `report.md` exist to prioritize
attention on future OCR batches, not to gate this one. For LoRA fine-tuning,
build instruction pairs from the per-document `.txt` files; for
pre-training, use the concatenated candidate file.

## Cost

Pilot-derived estimate: ~$0.0009–0.0015 per transcribed Badini page
(pricing constants in `ocr_config.py`; verify against the Vertex price
list). Non-Badini documents cost almost nothing: a few gated pages plus a
skip. Full queue upper bound ≈ $181 standard; in practice lower because
non-Badini books exit early. Set a Cloud Billing budget alert before large
runs and confirm the $300 welcome credit is linked:

```bash
gcloud billing projects describe bahdini-data \
  --format='yaml(billingAccountName,billingEnabled)'
```

## Suggested rollout

1. Pilot: `--max-pages 300` spread over sources, then `compile_corpus.py`
   and manually review `report.md` plus a sample of pages.
2. If quality holds, run source by source (spirez is 65% of the queue).
3. Re-run `build_manifest.py` + `run_ocr.py` as pending downloads
   (e.g. pertokenbadini) finish.
