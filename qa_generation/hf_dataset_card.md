---
language:
- ku
- kmr
license: other
task_categories:
- question-answering
- text-generation
pretty_name: Bahdini Kurdish Instruction-Tuning QA Pairs
size_categories:
- 100K<n<1M
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train-*.parquet
---

# Bahdini Kurdish QA pairs

952,801 instruction-tuning question–answer pairs in Bahdini (Badinî) Kurdish,
Arabic script, generated from a 246,515-chunk corpus of Bahdini text and
intended for a Gemma 4 31B IT LoRA fine-tune.

## Format

Each row is one training record, flattened from the source
`messages` + `metadata` JSONL:

| column | description |
|---|---|
| `system` | system prompt; differs between the two context modes |
| `user` | the model input: `Context: …\n\nQuestion: …`, or `Question: …` alone |
| `assistant` | the answer |
| `reasoning` | step-by-step justification, empty when the question does not need one. Maps to Gemma 4's native thought channel, not inlined into the answer |
| `document_id`, `chunk_id` | provenance back to the source chunk |
| `source` | which corpus the text came from |
| `question_type` | `factual`, `explanatory`, `summarization`, `definitional`, `inferential` |
| `context_mode` | `with_context` (70%) or `no_context` (30%) |

The 70/30 split is deliberate, covering two serving modes: retrieval-augmented
and bare-question. Every pair was generated with the full context visible to
the generator, so `no_context` pairs are still grounded.

## Composition

- **Question types**: factual 312,484 · inferential 185,568 · explanatory 174,425 · summarization 167,086 · definitional 113,234
- **With reasoning**: 508,061 (53.3%)
- **Mean prompt length**: 584 tokens (system + question + context), measured with the target tokenizer
- **Sources**: two Telegram book collections dominate (~69%), then `pertokenbadini`, `telegram_pertok_badini`, `facebook`, `zcks`, `spirez`, `sh2_unicodefixed_bahdini`

## Provenance

Source text is native PDF text extraction plus Gemini OCR of scanned Bahdini
books and posts. The corpus passed three corruption gates before generation:
a per-document chars/token check for legacy-font character soup, cp1252
mojibake recovery, and private-use-area / C0 control-character handling.

Pairs were generated with `google/gemini-3.1-flash-lite`, one call per chunk,
under a prompt requiring strictly Bahdini output, no Sorani, no general
Kurmanji, no Latin script.

## Known issues

Read these before training on it.

- **Latin-script leakage**: ~1.43% of answers contain at least one Latin
  character, against a prompt that forbids Latin script. Some are legitimate
  proper nouns or citations; the class has not been fully triaged.
- **Sorani source text**: a minority of source chunks are Sorani rather than
  Bahdini, concentrated in the `facebook` and `zcks` sources. The generated
  question and answer are still Bahdini, but for `with_context` rows a Sorani
  context sits inside the training prompt.
- **Off-list question types**: 4 rows carry `descriptive` or `comparative`,
  from a one-off generation experiment outside the main pipeline.
- **Duplicate questions** occur across chunks; they are not deduplicated.
- **0.7% of source chunks** produced no pairs at all (unparseable model output
  or repeated API failure) and are simply absent.

## Licensing

The underlying texts are third-party Bahdini publications collected from
public sources; their copyright status has not been individually cleared.
Treat this as research material and check rights before redistribution.
