# OCR Guide: Scanned PDFs to Bahdini Corpus

**This document is the single source of truth for OCR in this repository.**
It takes a new contributor (or an implementation agent) from zero Google
Cloud setup to a reviewed Bahdini text corpus, end to end.

> **Decision record (2026-07-15).** This guide originally described a Google
> Document AI batch workflow. After a side-by-side pilot, the project
> **diverged to Gemini visual transcription through Vertex AI** as the
> production OCR path. Document AI remains only as a retired reference in
> [Appendix A](#appendix-a-document-ai-retired-path). Everything else in this
> guide describes the Gemini pipeline in
> [`gemini_ocr_pipeline/`](../gemini_ocr_pipeline/).

## Why the project diverged to Gemini

An A/B comparison (pages 3–13 of a legacy Facebook scan, plus later smoke
tests; runner: [`scripts/compare_document_ai_gemini.py`](../scripts/compare_document_ai_gemini.py))
found:

| Criterion | Document AI Enterprise OCR | Gemini `gemini-3.1-flash-lite` (Vertex) |
|---|---|---|
| Bahdini character fidelity | Dropped Kurdish distinctions, word splitting, substitutions | Visibly better; remaining ڤ/ق confusions fixed by prompt `v4` |
| Corrupted legacy text layers | Must disable native parsing | Irrelevant: pages are sent as images only |
| Language filtering | None; transcribes everything | Prompt gate refuses non-Badini pages, then skips the document |
| Cost per page | $0.00150 flat | ≈ $0.0009–0.0015 per transcribed page; near zero for gated non-Badini books |
| Whole-queue estimate (201k pages) | ≈ $302 | ≤ $181, lower in practice because non-Badini books exit early |

Two standing technical rules came out of that evaluation:

1. **Never send a PDF to a model.** Many legacy PDFs carry malformed embedded
   text layers; a model that reads native PDF text inherits the corruption.
   Always render pages to images first. The pipeline does this for you.
2. **OCR output is never automatically training data.** Everything lands in
   an `_unreviewed` area and requires human review before promotion.

## Architecture

```text
raw PDFs (crawls/…)
   |
   |  scripts/extract_pipeline.py            (native text layer, no OCR)
   v
extractions/<source>/_manifest.jsonl         rows with status="needs_ocr"
   |
   |  gemini_ocr_pipeline/build_manifest.py  (Stage A)
   v
gemini_ocr_pipeline/output/manifest.jsonl    work queue: 1 row per PDF
   |
   |  gemini_ocr_pipeline/run_ocr.py         (Stage B)
   |    page -> grayscale PNG (~288 DPI) -> Gemini via Vertex AI
   v
gemini_ocr_pipeline/output/pages/<source>/<doc_id>.jsonl   1 record per page
   |
   |  gemini_ocr_pipeline/compile_corpus.py  (Stage C)
   v
gemini_ocr_pipeline/output/corpus_unreviewed/              per-doc .txt,
   |                                          corpus.jsonl, report.md,
   |                                          pretrain_candidate_unreviewed.txt
   |
   |  HUMAN REVIEW                            (Stage D, mandatory)
   v
training corpus (LoRA fine-tuning / pre-training)
```

Do not OCR every PDF. The native extraction pipeline is faster and free for
files with a usable text layer; only documents it flags `needs_ocr` enter
this pipeline. `gemini_ocr_pipeline/output/` is Git-ignored and fully
regenerable except for the Gemini responses themselves, which cost money —
treat `output/pages/` as the valuable artifact and back it up before bulk
deleting anything.

## 1. One-time cloud setup (A to Z)

Identifiers used throughout (already provisioned for this project):

| Setting | Value |
|---|---|
| Google Cloud project | `bahdini-data` (project number `377090410782`) |
| API | `aiplatform.googleapis.com` (Vertex AI) |
| Model | `gemini-3.1-flash-lite`, called with `vertexai=True`, `location="global"` |
| Authentication | Application Default Credentials (ADC) |
| Conda environment | `ai` (never create a project-local venv) |
| Buckets / processors | **None needed** for the Gemini path |

Steps, in order. Skip any step that is already verified.

1. **Install and authenticate the gcloud CLI.**

   ```bash
   gcloud auth login
   gcloud config set project bahdini-data
   gcloud config list --format='text(core.project,core.account)'
   ```

2. **Confirm billing and set a budget alert before any large run.**

   ```bash
   gcloud billing projects describe bahdini-data \
     --format='yaml(billingAccountName,billingEnabled)'
   ```

   Then create a budget with alerts (for example $50) in Console → Billing →
   Budgets & alerts. Vertex AI calls to Google-owned Gemini models are
   expected to be eligible for the $300 Google Cloud welcome credit; the
   AI Studio Gemini API is **not**. Verify in Billing → Reports after the
   pilot that usage is actually drawing from the credit.

3. **Enable the Vertex AI API.**

   ```bash
   gcloud services enable aiplatform.googleapis.com --project=bahdini-data
   gcloud services list --enabled --project=bahdini-data \
     --filter='config.name=aiplatform.googleapis.com' \
     --format='value(config.name)'
   ```

4. **Create Application Default Credentials.**

   ```bash
   gcloud auth application-default login
   gcloud auth application-default print-access-token >/dev/null && echo "ADC is valid"
   ```

   Credentials are stored outside the repository at
   `~/.config/gcloud/application_default_credentials.json`. Never commit
   credentials. If a local browser cannot complete the login, run
   `gcloud auth application-default login --no-browser`, run the printed
   `--remote-bootstrap` command on a browser-capable machine, and paste the
   authorization **response** (not the URL) back into the first terminal.

5. **Install the Python dependencies in the `ai` environment.**

   ```bash
   conda activate ai
   python -m pip install --upgrade google-genai pymupdf
   ```

6. **Verify the whole chain with one tiny call.**

   ```bash
   conda run --no-capture-output -n ai python - <<'PY'
   from google import genai
   client = genai.Client(vertexai=True, project="bahdini-data", location="global")
   r = client.models.generate_content(model="gemini-3.1-flash-lite", contents="ping")
   print("Vertex AI OK:", r.text[:40])
   PY
   ```

   This exercises ADC, API enablement, project selection, and model access at
   once. If it fails, see the [troubleshooting map](#7-troubleshooting-map).

## 2. Configuration and provenance rules

All knobs live in [`gemini_ocr_pipeline/ocr_config.py`](../gemini_ocr_pipeline/ocr_config.py):
model, Vertex project/location, rendering resolution (≈288 DPI grayscale,
long side capped at 3400 px), pricing constants, and the transcription
prompt.

The prompt is **versioned** (`PROMPT_VERSION`, currently `v4`) and every page
record stores the version that produced it. Rules:

- Any change to the prompt, model, or rendering that can affect output
  **must** bump `PROMPT_VERSION`.
- `v3` introduced the language gate: the model answers
  `[NOT_BADINI: <language>]` instead of transcribing pages whose body text is
  Arabic, Sorani, Latin-script Kurmanji, Persian, Turkish, or English.
- `v4` added the confusable-letter dot-count rule after a smoke test showed
  systematic ڤ→ق substitutions; on the test page it moved counts from
  ڤ=8/ق=10 to ڤ=18/ق=1 with the single remaining ق being genuinely correct.
- The pricing constants ($0.30 / $1.35 per million input/output tokens)
  reproduce the pilot's measured cost but are **estimates** — verify against
  the current Vertex AI price list before trusting absolute dollar numbers.

## 3. Stage A — Build the work queue

```bash
conda run --no-capture-output -n ai python gemini_ocr_pipeline/build_manifest.py
```

Reads every `extractions/<source>/_manifest.jsonl`, keeps rows with
`status="needs_ocr"` whose PDF still exists on disk, and writes
`gemini_ocr_pipeline/output/manifest.jsonl` (one row per document with
source, relative path, stable `doc_id`, page and byte counts). Missing files
are counted and reported, not fatal — sources with incomplete downloads
(for example `pertokenbadini`) simply queue what exists.

Re-run Stage A whenever new downloads finish or the extraction pipeline is
re-run. It is cheap and idempotent. Queue at the time of writing: **2,869
documents / 201,327 pages** (spirez ≈ 65% of documents).

## 4. Stage B — Run OCR

The runner is resumable and safe to interrupt at any point: every finished
page is already on disk, and a re-run resumes exactly where it stopped.

```bash
# pilot: a bounded number of pages
conda run --no-capture-output -n ai python -u gemini_ocr_pipeline/run_ocr.py \
  --source zcks --max-pages 50 --workers 2

# production, per source
conda run --no-capture-output -n ai python -u gemini_ocr_pipeline/run_ocr.py \
  --source spirez --workers 4
```

| Flag | Meaning |
|---|---|
| `--source SRC` | limit to one source (repeatable) |
| `--doc SUBSTRING` | only documents whose path contains the substring |
| `--max-docs N` | stop after N documents |
| `--max-pages N` | global page budget for this run (use for pilots) |
| `--workers N` | concurrent Gemini requests (default 2) |
| `--skip-after N` | language gate: skip the rest of a document after N consecutive non-Badini pages (default 5; 0 disables) |
| `--ignore-doc-skips` | re-attempt documents previously skipped as non-Badini |
| `--keep-images` | keep rendered PNGs under `output/images/<doc_id>/` |
| `--dry-run` | render and record pages without calling Gemini |

Every page attempt appends one JSONL record to
`output/pages/<source>/<doc_id>.jsonl` containing the model, prompt version,
image SHA-256 and pixel size, finish reason, token usage, estimated cost,
timestamp, status, and the raw transcription. Page statuses:

| Status | Meaning | Retried on re-run? |
|---|---|---|
| `ok` | transcription received | no |
| `no_text` | model answered `[NO_TEXT]` (cover, pure image) | no |
| `blank` | page image was blank; Gemini never called | no |
| `not_badini` | model answered `[NOT_BADINI: <language>]` | no |
| `empty` | empty response (for example token-limit cutoff) | yes |
| `error` | exception after 5 retries with backoff | yes |
| `dry_run` | rendered only | yes |
| `doc_skipped` | record with `page: 0`; the language gate abandoned the document | only with `--ignore-doc-skips` |

The language gate is the main cost control: a non-Badini book costs a handful
of gated pages (a few output tokens each) instead of a full transcription.
Verified example: a 1,002-page Arabic novel cost $0.0024 total. Blank and
`no_text` pages are neutral — they neither extend nor break a non-Badini run,
so an Arabic book with an ornamental cover is still caught.

## 5. Stage C — Compile the corpus

```bash
conda run --no-capture-output -n ai python gemini_ocr_pipeline/compile_corpus.py
```

Assembles all page records into `output/corpus_unreviewed/`:

- `<source>/<document>.txt` — pages joined with `\n\f\n` (same separator as
  the native extraction corpus), normalized with the same NFKC + KLPT rules
  as `extract_pipeline.py` so both corpora can be mixed (`--no-normalize` to
  keep raw Gemini output; note KLPT also folds Arabic-Indic digits like ١١٤
  to 114).
- `corpus.jsonl` — one record per document: page counts by status,
  completeness, character count, `[unclear]` count, Arabic-script and
  Kurdish-letter ratios, estimated cost, `review_status: "unreviewed"`, and a
  classification: `kurdish`, `not_badini`, `arabic_not_kurdish`, `low_text`,
  or `not_arabic_script`.
- `report.md` — per-source totals plus a "review first" list (non-Kurdish
  classifications, heavy `[unclear]` use, incomplete documents).
- `pretrain_candidate_unreviewed.txt` — concatenation of complete,
  Kurdish-classified documents.

Stage C is a pure local re-aggregation: re-run it as often as you like, it
never calls the API.

## 6. Stage D — Human review and promotion (mandatory)

Classification prioritizes review; it never replaces it. Before any text is
used for training:

1. Read `report.md`; resolve every "review first" entry (re-run failed pages,
   confirm `not_badini` skips by spot-checking one page image with
   `--keep-images`, decide what to do with `low_text` fragments).
2. Sample transcribed pages against the source PDF — render the page and
   compare. Check specifically: Kurdish letters `ێ ۆ ڕ ڵ ڤ پ چ ژ گ ە`,
   reading order across columns, poetry line breaks, and that the model did
   not paraphrase or "repair" anything.
3. Only after review, copy or reference the accepted `.txt` files into the
   training candidate set. For LoRA fine-tuning, build instruction pairs from
   the reviewed per-document files; for pre-training, use the reviewed
   concatenated file.
4. Record what was accepted (a simple reviewed-list file next to the corpus
   is enough) so a later compile re-run cannot silently change the training
   set.

Acceptance checks for a pilot batch:

| Check | Expected outcome |
|---|---|
| Kurdish character fidelity | ڤ/ق, پ/ب, چ/ج, گ/ک, ژ/ز, ێ/ی, ۆ/و all correct in samples |
| Reading order | Sampled paragraphs follow the visible page order |
| No hallucination | Nothing in the transcript that is not on the page; `[unclear]` used instead of guesses |
| Language gate precision | Spot-checked `not_badini` pages really are non-Badini |
| Provenance | Every output maps to one source PDF, page, prompt version, and image hash |
| Re-run safety | Re-running the same command submits no completed page again |
| Failure visibility | Errors exist as page records, not silent gaps |

## 7. Troubleshooting map

| Symptom | Likely cause | Resolution |
|---|---|---|
| `DefaultCredentialsError` | ADC absent or expired | `gcloud auth application-default login`, verify with `print-access-token` |
| `PermissionDenied` / API disabled | Vertex AI API off, wrong project | Enable `aiplatform.googleapis.com` in `bahdini-data`; check `gcloud config list` |
| `NOT_FOUND` for the model | Model name typo or unavailable in `global` | Check `GEMINI_MODEL` in `ocr_config.py` |
| `429 RESOURCE_EXHAUSTED` | Quota / rate limit | Lower `--workers`; the runner already retries with exponential backoff |
| Many `empty` pages, `finish_reason: MAX_TOKENS` | Extremely dense pages hit the output cap | Raise `MAX_OUTPUT_TOKENS` in `ocr_config.py`; affected pages retry on the next run |
| Many `error` records for one PDF | Corrupt/truncated download | Confirm with `python -m fitz` open; re-download, re-run extraction, Stage A, Stage B |
| Transcripts look "too clean" (headers gone, digits Western) | Working as designed | Prompt drops running headers/page numbers; KLPT converts digits — use `--no-normalize` if unwanted |
| A Badini book was skipped as `not_badini` | Gate false positive (for example long Arabic preface) | Re-run with `--ignore-doc-skips --doc '<name>'`, review, consider raising `--skip-after` |
| Costs higher than estimated | Pricing constants are estimates | Compare Billing → Reports against `est_cost_usd` sums; update constants in `ocr_config.py` |
| Browser login fails on Linux | Headless/remote session | Use the `--no-browser` remote-bootstrap flow described in step 1.4 |

## 8. Operational checklist

Before a production-scale run:

- [ ] Billing enabled, welcome-credit linkage confirmed, budget alert set.
- [ ] `aiplatform.googleapis.com` enabled in `bahdini-data`; ADC valid.
- [ ] `google-genai` and `pymupdf` installed in the `ai` conda environment.
- [ ] Stage A re-run after the latest downloads; queue counts reviewed.
- [ ] A pilot (`--max-pages` ≈ 300 across mixed sources) passed Stage D
      review, including the acceptance checks above.
- [ ] `PROMPT_VERSION` is what the pilot validated; no unversioned prompt edits.
- [ ] Production runs go source by source with a bounded `--workers`.
- [ ] `output/pages/` is backed up (it is the paid artifact).
- [ ] Nothing from `corpus_unreviewed/` enters training without Stage D review.

## Appendix A: Document AI (retired path)

Document AI Enterprise OCR was fully set up and evaluated before the project
diverged to Gemini. It is **not used** for production OCR. Kept for
reference and possible re-evaluation:

- Processor: `bahdini-enterprise-ocr`, type `OCR_PROCESSOR`, state `ENABLED`,
  location `us`, processor ID `6c2e13121ee43056`, resource name
  `projects/377090410782/locations/us/processors/6c2e13121ee43056`.
- API `documentai.googleapis.com` is enabled; no Cloud Storage buckets were
  ever created (the evaluation used synchronous processing only).
- Comparison runner:
  [`scripts/compare_document_ai_gemini.py`](../scripts/compare_document_ai_gemini.py)
  — processes a page range with both providers and saves artifacts for
  side-by-side review. Use it to re-benchmark if either provider changes
  materially.
- If Document AI is ever revisited: keep `enable_native_pdf_parsing=False`
  (legacy text layers are corrupt), use the location-specific endpoint
  (`us-documentai.googleapis.com`), reconstruct text via text anchors by
  joining every `text_segments` slice (missing `start_index` means 0), and
  keep `\n\f\n` page separators. Batch processing would additionally need
  input/output buckets co-located with the processor and
  `roles/storage.objectViewer` / `roles/storage.objectCreator` for
  `service-377090410782@gcp-sa-documentai.iam.gserviceaccount.com`.
- Retirement rationale: more Bahdini character errors than Gemini, no
  language gating, and ≈ 40% higher cost at equal or worse quality
  (measured $0.00150/page vs ≈ $0.00090/page in the pilot).

## Project status: 2026-07-15

Gemini pipeline built and smoke-tested end to end (a 4-page Badini magazine
document transcribed correctly under prompt `v4`; a 1,002-page Arabic novel
gated and skipped for $0.0024). Work queue: 2,869 documents / 201,327 pages,
all present on disk. The full production run has **not** been started; the
next action is the ~300-page mixed-source pilot followed by Stage D review.
