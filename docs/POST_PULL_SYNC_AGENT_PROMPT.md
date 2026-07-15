# Post-Pull Sync Agent Prompt

You are synchronizing an older local checkout of the Bahdini Crawler after it
has pulled the current repository. The repository layout and processing
workflow have changed. Perform a careful migration in place.

Your goal is to make the local checkout usable with the current code and
documentation while preserving all existing local data. Do not delete, move,
overwrite, reset, clean, or discard data until you have explicitly identified
what it is and shown that a replacement exists.

Important repository facts:

- `crawls/` is now the canonical home for all raw collected data.
- Website crawl data belongs at `crawls/<site>/`, with downloaded files in
  `crawls/<site>/documents/`.
- Facebook PDFs belong at `crawls/facebook/pdfs/`.
- Telegram downloads belong at `crawls/telegram/downloads/<channel>/`.
- The current extraction entry point is `scripts/extract_pipeline.py`.
- Native extraction outputs are under `extractions/<source>/`; generated
  `.txt` outputs are intentionally ignored by Git and can be regenerated.
- `extractions/needs_ocr.csv` is the canonical queue for PDFs needing OCR.
- `document_ai_sample/`, raw document directories, browser sessions, Telegram
  sessions, `.env` files, and extracted `.txt` files are ignored by Git. Their
  absence from `git status` does not mean they are safe to remove.
- The local developer environment uses the Conda environment named `ai`. Do
  not create a project-local virtual environment.
- Read `README.md`, `extractions/README.md`, and
  `docs/DOCUMENT_AI_OCR_GUIDE.md` before deciding how any old directory maps
  to the current workflow.

Work in the following order.

1. Establish a non-destructive baseline.
   - Run `git status --short`, `git log --oneline -10`, and `git diff --stat`.
   - Record the repository root, current branch, and current commit.
   - List top-level directories and locate any old raw-data locations without
     modifying them.
   - Inspect `.gitignore` so ignored local assets are included in the
     inventory. Use `find` or `du` only for inspection.
   - Do not use `git reset --hard`, `git clean`, `git checkout --`, `rm -rf`,
     or any other destructive cleanup command.

2. Inventory the local material that must be preserved.
   - Identify every directory containing PDFs, downloaded documents, Telegram
     channel files, Facebook PDFs, crawl metadata, extracted `.txt` files,
     OCR outputs, browser profiles, or local credentials.
   - For each old location, determine whether its contents are already present
     in the current canonical location, partially present, or absent there.
   - Compare file counts and total byte sizes before proposing any move or
     copy. When practical, compare checksums for a small representative sample.
   - Treat unknown directories as user data until proven otherwise.

3. Map old paths to the current layout.
   - Prefer existing repository documentation and current scripts over guesses.
   - Preserve relative paths and source provenance when migrating raw files.
   - Use a non-destructive copy first if there is any uncertainty. Do not
     remove the old source until the user explicitly approves it after
     successful validation.
   - If an old directory does not map unambiguously, leave it untouched and
     report the ambiguity instead of inventing a destination.
   - Do not duplicate tens of gigabytes unnecessarily: if an old path and new
     canonical path are on the same filesystem, consider a symlink only after
     confirming that the current scripts follow it correctly. Explain this
     choice before making it. Prefer an actual canonical directory layout for
     long-term use.

4. Restore reproducible derived outputs only after raw data is correctly
   located.
   - Do not copy old generated extraction text blindly into the new layout.
   - First verify the current raw input locations expected by
     `scripts/extract_pipeline.py`.
   - Run a small, scoped dry/probe extraction if supported; otherwise use a
     limited current-source run to confirm discovery and output paths.
   - Run the current extraction pipeline in the `ai` Conda environment only
     when raw inputs are confirmed and the expected output behavior has been
     stated. Existing manifests and generated text must not be overwritten
     without a reasoned, recoverable plan.
   - Rebuild generated `.txt` files from raw PDFs when needed. Keep the old
     generated files as a rollback source until the regenerated results have
     been checked.

5. Validate the synchronized checkout.
   - Confirm required current directories exist and the current scripts see
     the local raw data.
   - Check that `scripts/extract_pipeline.py --classify-only` can run in the
     `ai` environment when its prerequisites and relevant manifests are
     present.
   - Confirm that the relevant `extractions/<source>/_manifest.jsonl` records
     and `extractions/needs_ocr.csv` are coherent with available data.
   - Run `git diff --check` and report only repository changes you actually
     made. Do not stage, commit, or modify unrelated user work.

6. Report clearly before considering any cleanup.
   Your final report must include:
   - the local directories found and their chosen canonical destinations;
   - what was copied, linked, regenerated, or deliberately left untouched;
   - file-count and byte-size comparisons used for each migration;
   - commands/tests run and their results;
   - unresolved ambiguities, missing dependencies, or permission blockers;
   - a separate list of old directories that could be deleted only after user
     review. Do not delete them yourself.

Document AI is optional at this stage. Do not upload any document, create a
processor, create buckets, enable billing, or submit OCR work unless the user
explicitly asks. If OCR setup is requested, follow
`docs/DOCUMENT_AI_OCR_GUIDE.md` and process a small pilot batch before any
large submission.

Begin by presenting the baseline inventory and one concrete migration plan.
Then make the smallest reversible change, validate it immediately, and
continue iteratively.
```