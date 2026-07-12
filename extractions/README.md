# extractions/

Normalized plain-text corpus produced by `scripts/extract_pipeline.py`
(run inside the conda `ai` env). One subfolder per source, one `.txt`
per input document:

| folder | input | documents |
|---|---|---|
| `telegram_badini_book/` | telegram/downloads/Badini_book | 993 PDFs |
| `telegram_jihana_pertuken_pdf/` | telegram/downloads/jihana_pertuken_pdf | 774 PDFs |
| `telegram_pertok_badini/` | telegram/downloads/pertok_badini | 157 PDFs |
| `sh2_unicodefixed_bahdini/` | Sh2_UnicodeFixed_Bahdini | 241 txt |

Text is extracted with PyMuPDF (embedded text layer only, no OCR), then
NFKC-folded and normalized with [KLPT](https://github.com/sinaahmadi/klpt)
(Sorani/Arabic-script rules, same alphabet as Bahdini): Arabic-only letter
variants ي/ك/ة become Kurdish ی/ک/ە, presentation forms are folded, ZWNJ
usage is standardized. Pages are separated by form-feed (`\f`) lines.

## Per-document status (`_manifest.jsonl` in each folder)

- `extracted` — clean text layer, output written.
- `extracted_suspect` — output written, but >20% of Arabic characters were
  presentation forms, so the reading order may be visual instead of logical.
  Verify before trusting; candidates for re-doing with Document AI.
- `extracted_partial` — output written, but >50% of pages had no text
  (mixed scanned/text document).
- `needs_ocr` — no usable text layer (scanned book); no output written.
  All of these are listed in `needs_ocr.csv` for a future Google Cloud
  Document AI pass.
- `error` — file could not be opened (corrupt/password-protected).

Manifest rows also carry `chars`, `chars_per_page`, `empty_page_ratio`,
`presentation_form_ratio`, `arabic_script_ratio` and `kurdish_chars`
(count of letters that exist in Kurdish but not Arabic — a low count on a
large document suggests an Arabic-language book or a legacy font encoding
where e.g. پ/گ were stored as ث/ط).

`extraction_summary.json` aggregates counts per source. The `.txt` outputs
are gitignored (regenerate with the script); manifests and summaries are
tracked.

Re-runs are incremental (already-manifested files are skipped); use
`--force` to redo, `--source <name>` to limit, `--limit N` to sample.
