# QA-pair generation pipeline

Turns the clean Bahdini text corpus into instruction-tuning QA pairs for the
partner's Gemma 4 31B IT LoRA fine-tune, in the JSONL schema confirmed over
email (a `messages` list plus a `metadata` object). Mirrors the shape of
[gemini_ocr_pipeline/](../gemini_ocr_pipeline/): versioned prompt/config,
JSONL work queue, resumable per-document generation records, a compile step.

## Stages

```text
extractions/*/safe/*.txt                    native-extraction "safe" corpus
gemini_ocr_pipeline/.../corpus_unreviewed/   OCR corpus, reviewed rows only
        |  build_chunks.py
        v
output/chunks.jsonl                        work queue, one row per context chunk
        |  generate_qa_openrouter.py        resumable; chunk -> Gemini -> QA pairs
        v
output/generations/<source>/<doc_id>.jsonl one record per chunk attempt
        |  compile_qa_dataset.py
        v
output/dataset/qa_pairs.jsonl              final messages+metadata JSONL
output/dataset/sample.jsonl                first N records, for partner review
```

## The chunk queue

```bash
python3 qa_generation/build_chunks.py
```

Source pool, by design:

- `extractions/<source>/safe/*.txt` — native PDF text extraction, already
  classified `safe`. Included unconditionally.
- `gemini_ocr_pipeline/output/corpus_unreviewed/` — only rows with
  `classification == "kurdish"` **and** `review_status == "reviewed"`. That
  pipeline's own rule is that nothing OCR'd is promoted to training data
  automatically; as of this writing every OCR'd document is still
  `unreviewed`, so none of it feeds the chunk queue by default.
  `--include-unreviewed-ocr` opts into unreviewed-but-high-completeness OCR
  text anyway (prints a loud warning) — useful only for a quick throwaway
  sample, never for the delivered dataset.

Chunking is paragraph-aware (splits on the extraction pipeline's page-break
`\f` markers, then blank lines, then sentence boundaries for an oversized
paragraph) and targets `qa_config.TARGET_CHUNK_TOKENS` (700, cap 850, floor
120) — sized so a full QA record (system + context + question + answer)
lands near the partner's ~1,000-token/record estimate. Token counts are
character-based estimates (`qa_config.CHARS_PER_TOKEN = 3.2`, derived from
this corpus's own words/chars ratio and `scripts/token_estimate.py`'s
tokens/word rule of thumb) — re-derive from an actual tokenizer before
trusting it for anything cost-critical.

Current run over the safe-extraction pool: 1,853 documents -> 102,111 chunks
(~62.6M estimated context tokens). See `output/chunks_report.md` for the
per-source breakdown after running.

## Generation

```bash
python3 qa_generation/generate_qa_openrouter.py --max-chunks 20   # a quick sample first
python3 qa_generation/generate_qa_openrouter.py --budget-usd 25 --concurrency 16
```

Calls Gemini through OpenRouter (reuses `OPENROUTER_API_KEY` from `.env`,
same as `gemini_ocr_pipeline/run_ocr_openrouter.py`) with the prompt in
`qa_config.QA_GENERATION_PROMPT_TEMPLATE`, requesting `--pairs-per-chunk`
(default 3) QA pairs as a strict JSON list per chunk. Resumable: each
attempted chunk is appended to
`output/generations/<source>/<document_id>.jsonl`, and a re-run skips
`chunk_id`s already recorded there. `--source`, `--origin`, `--max-chunks`,
and `--budget-usd` all narrow scope, so a small representative sample can be
produced (and sent to the partner) well before the full run.

`qa_config.OPENROUTER_MODEL` currently points at
`google/gemini-3.1-pro-preview` — a stronger tier than the OCR pipeline's
`flash-lite`, since QA generation leans more on instruction-following and
reasoning than transcription. Verify that model slug and the placeholder
per-token pricing in `qa_config.py` against OpenRouter's current catalog
before running at any real budget.

## Compiling the dataset

```bash
python3 qa_generation/compile_qa_dataset.py --sample-size 20
```

Wraps every generated `{question, answer, question_type}` pair with its
source chunk's text into the agreed schema:

```json
{
  "messages": [
    {"role": "system", "content": "Answer the question in Bahdini Kurdish using the supplied context."},
    {"role": "user", "content": "Context: <chunk text>\n\nQuestion: <question>"},
    {"role": "assistant", "content": "<answer>"}
  ],
  "metadata": {
    "document_id": "<source document id>",
    "chunk_id": "<source chunk id>",
    "source": "<source name, e.g. facebook, zcks, telegram_badini_book>",
    "question_type": "<factual, explanatory, summarization, definitional, inferential>"
  }
}
```

Writes `output/dataset/qa_pairs.jsonl` (the full set), `sample.jsonl` (first
N records — this is what to hand the partner for early review), and
`report.md` (counts per source/question_type, plus how many records exceed a
1,200-token flag threshold against the ~1,000-token/record target).

## Assumptions baked in here — confirm before scaling up

The email thread didn't get a direct answer on several points before this
groundwork was built; defaults were chosen conservatively and are easy to
change in `qa_config.py`:

1. **Token budget** (~1,000/record) is treated as covering the *complete*
   record — system + context + question + answer — not context alone.
2. **Every QA pair carries its supporting context** in the `user` message
   (matches the schema the partner already confirmed works for them).
3. **Answers are extractive-first**, with reasonable inference/synthesis
   allowed for `explanatory`/`summarization` questions but never introducing
   facts absent from the context (instructed directly in the generation
   prompt).
4. **Question types**: `factual, explanatory, summarization, definitional,
   inferential` (`qa_config.QUESTION_TYPES`) — a superset of the partner's
   example list ("factual, explanatory, summarization, etc.").

If the partner's actual answers to those clarifying questions differ,
update `qa_config.py` (prompt template, `QUESTION_TYPES`,
`TARGET_CHUNK_TOKENS`) and re-run — nothing downstream needs to change
shape.
