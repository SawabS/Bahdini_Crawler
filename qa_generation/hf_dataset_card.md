---
language:
- ku
- kmr
license: other
license_name: research-use-uncleared-rights
license_link: LICENSE
pretty_name: Bahdini Kurdish Instruction-Tuning QA Pairs
task_categories:
- question-answering
- text-generation
task_ids:
- extractive-qa
- closed-domain-qa
size_categories:
- 100K<n<1M
tags:
- kurdish
- bahdini
- badini
- kurmanji
- arabic-script
- instruction-tuning
- low-resource
- synthetic
annotations_creators:
- machine-generated
language_creators:
- found
source_datasets:
- original
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train-*.parquet
dataset_info:
  features:
  - name: system
    dtype: string
  - name: user
    dtype: string
  - name: assistant
    dtype: string
  - name: reasoning
    dtype: string
  - name: document_id
    dtype: string
  - name: chunk_id
    dtype: string
  - name: source
    dtype: string
  - name: question_type
    dtype: string
  - name: context_mode
    dtype: string
  splits:
  - name: train
    num_examples: 952801
---

<div align="center">

# Bahdini Kurdish QA Pairs

**952,801 instruction-tuning records in Bahdini (Badinî) Kurdish, Arabic script**

`644.3M tokens` · `238,309 source chunks` · `4,306 documents` · `8 corpora`

</div>

---

Bahdini is the Kurmanji variety spoken in Dohuk city and governorate. It is a
low-resource variety with very little machine-readable instruction data, and
this set was built to fine-tune a Gemma 4 31B IT LoRA on it.

Every pair was generated from a real passage of Bahdini text, under a prompt
that requires strictly Bahdini output and forbids Sorani vocabulary, general
Kurmanji, and Latin script.

## Quick start

```python
from datasets import load_dataset

ds = load_dataset("SawabS/Bahdini_QA_Pairs", split="train")
print(ds)
print(ds[0]["user"])
print(ds[0]["assistant"])
```

Chat-format training, using the columns directly:

```python
def to_messages(row):
    assistant = {"role": "assistant", "content": row["assistant"]}
    if row["reasoning"]:
        assistant["reasoning"] = row["reasoning"]   # Gemma's native thought channel
    return {"messages": [
        {"role": "system", "content": row["system"]},
        {"role": "user",   "content": row["user"]},
        assistant,
    ]}

chat = ds.map(to_messages, remove_columns=ds.column_names)
```

## Fields

| Column | Description |
|---|---|
| `system` | System prompt. Differs between the two context modes. |
| `user` | The model input: `Context: ...\n\nQuestion: ...`, or `Question: ...` alone. |
| `assistant` | The answer, in Bahdini. |
| `reasoning` | Step-by-step justification, empty when the question does not need one. Maps to Gemma 4's native thought channel rather than being inlined into the answer. |
| `document_id`, `chunk_id` | Provenance back to the source passage. |
| `source` | Which corpus the passage came from. |
| `question_type` | `factual`, `explanatory`, `summarization`, `definitional`, `inferential`. |
| `context_mode` | `with_context` (70%) or `no_context` (30%). |

### The two context modes

70% of records carry a `Context:` block; 30% are a bare question. This is
deliberate, covering both serving modes: retrieval-augmented and standalone. A
model trained only on context-bearing prompts tends to refuse or hedge when the
context is absent.

Every pair was generated with the full passage visible to the generator, so the
bare-question records are still grounded in real source text even though that
text is not shown at training time.

| `context_mode` | Records | Share | Mean tokens/record |
|---|---:|---:|---:|
| `with_context` | 667,214 | 70.03% | 882 |
| `no_context` | 285,587 | 29.97% | 134 |

## Composition

| `question_type` | Pairs | Share |
|---|---:|---:|
| factual | 312,484 | 32.8% |
| inferential | 185,568 | 19.5% |
| explanatory | 174,425 | 18.3% |
| summarization | 167,086 | 17.5% |
| definitional | 113,234 | 11.9% |

| Source corpus | Pairs | Share |
|---|---:|---:|
| telegram_badini_book | 350,085 | 36.7% |
| telegram_jihana_pertuken_pdf | 307,525 | 32.3% |
| pertokenbadini | 85,524 | 9.0% |
| telegram_pertok_badini | 81,012 | 8.5% |
| facebook | 60,442 | 6.3% |
| zcks | 33,971 | 3.6% |
| spirez | 18,728 | 2.0% |
| sh2_unicodefixed_bahdini | 15,514 | 1.6% |

> **Stratify any train/eval split by `source`.** Two Telegram book collections
> are 69% of the corpus, so an unstratified split measures those two and
> nothing else.

## Size and shape

| | |
|---|---:|
| Records | 952,801 |
| Total tokens (Gemma 4 tokenizer) | 644,306,453 |
| Records with `reasoning` | 508,061 (53.3%) |
| Mean tokens per record | 658 |
| Median / p95 | 793 / 1,117 |

Record length is **bimodal**, not centred on its mean: bare-question records
cluster near 90 tokens and context-carrying ones near 950. The mean of 658
falls in the valley between them and describes almost no actual record. Batch
and report the two populations separately.

The user message is 82% of the whole token budget, so any change to context
filtering affects nearly the entire dataset, while a change to answers or
reasoning touches at most a seventh of it.

## Known issues

Please read this section before training. None of these are resolved.

103,357 records (10.85%) carry at least one quality flag. 5,750 carry two or
more, and those are the highest-value rows to inspect first, because
independent signals agreeing is stronger evidence than any single heuristic.

| Flag | Rows | Nature |
|---|---:|---|
| suspected Sorani answer | 41,512 | heuristic |
| suspected Sorani context | 28,272 | heuristic, the case that reaches training |
| duplicate question | 25,441 | objective |
| Latin script in answer | 13,593 | objective, violates the prompt |
| off-pipeline model | 391 | objective |
| over-length prompt | 27 | objective |
| off-list `question_type` | 4 | objective |

**Sorani source text.** A minority of source passages are Sorani rather than
Bahdini. The generator handles this correctly and still writes the question and
answer in Bahdini, but for `with_context` records the raw passage goes into the
training prompt, so Sorani text enters the input. Concentrated in two sources:

| Source | Suspected Sorani context |
|---|---:|
| facebook | 21.0% |
| zcks | 13.8% |
| spirez | 2.6% |
| pertokenbadini | 1.7% |
| telegram_badini_book | 1.6% |
| sh2_unicodefixed_bahdini | 1.2% |
| telegram_jihana_pertuken_pdf | 0.9% |
| telegram_pertok_badini | 0.6% |

The cheapest mitigation is to force affected rows to `no_context`, which keeps
the Bahdini question and answer and discards only the Sorani prompt text. Note
that these figures come from a marker-frequency heuristic with no validated
threshold, so treat them as a queue to inspect rather than a count of bad rows.

**Latin-script leakage.** About 1.43% of answers contain at least one Latin
character, against a prompt that forbids Latin script. The class is mixed:
proper nouns and citations alongside genuine leakage.

**Duplicate questions.** 2.7% of rows repeat a question string seen earlier.
Do **not** deduplicate on the question alone: the repeats are dominated by
generic questions askable of almost any passage, which arise independently
across unrelated passages and carry entirely different answers. Deduplicate on
`(question, answer)` or within a document.

**Off-schema rows.** 4 rows carry a `question_type` outside the five listed
above, and 391 rows came from a one-off generation experiment outside the main
pipeline. Filter on `question_type` if strict conformance matters.

**Coverage.** 0.7% of source passages produced no pairs at all, through
unparseable model output or repeated API failure, and are simply absent.

## How it was built

| | |
|---|---|
| Generator | `google/gemini-3.1-flash-lite` |
| Calls | 248,243, one per source passage, 4 pairs requested each |
| Tokens billed | 304.5M input, 150.2M output |
| Cost | $301.41, about $0.32 per 1,000 pairs |
| Passage size | ~900 tokens, packed paragraph-first |

Source text is native PDF text extraction plus Gemini OCR of scanned Bahdini
books and posts. The corpus passed three corruption gates before generation: a
per-document chars-per-token check for legacy-font character soup, cp1252
mojibake recovery, and private-use-area and C0 control-character handling.

## Files

| Path | Contents |
|---|---|
| `data/train-*.parquet` | The dataset, 4 shards, 345 MB. Read by `load_dataset`. |
| `raw/qa_pairs.jsonl` | The same 952,801 records in the original nested `messages` + `metadata` form, 2.4 GB. Verified row for row against the Parquet. |

## Licensing and intended use

The underlying texts are third-party Bahdini publications collected from public
sources, and their copyright status has **not** been individually cleared. Treat
this as research material for low-resource language work, and verify rights
before redistributing or using commercially.

The pairs are machine-generated and have not been reviewed by a native speaker
at scale. They are suitable for fine-tuning experiments, not as a gold-standard
evaluation set.
