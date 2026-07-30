"""Shared configuration for the QA-pair generation pipeline.

Turns the clean Bahdini text corpus (native "safe" extractions, plus the
reviewed Gemini OCR corpus) into context chunks, then into instruction-tuning
QA pairs for the Gemma 4 31B IT LoRA fine-tune.
Mirrors the layout of gemini_ocr_pipeline/ (versioned prompt/config, JSONL
work queue, resumable per-source generation records, a compile step) so both
pipelines read the same way.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "qa_generation" / "output"
CHUNKS_PATH = OUTPUT_DIR / "chunks.jsonl"
CHUNKS_REPORT_PATH = OUTPUT_DIR / "chunks_report.md"
GENERATIONS_DIR = OUTPUT_DIR / "generations"
DATASET_DIR = OUTPUT_DIR / "dataset"

EXTRACTIONS_DIR = ROOT / "extractions"
OCR_CORPUS_DIR = ROOT / "gemini_ocr_pipeline" / "output" / "corpus"
OCR_CORPUS_JSONL = OCR_CORPUS_DIR / "corpus.jsonl"

# --- token estimation -------------------------------------------------
#
# gemma_tokenizer.py loads the actual google/gemma-4-31B-it tokenizer and is
# what build_chunks.py and compile_qa_dataset.py use for real token counts --
# this constant is only the fallback for when that tokenizer can't be
# loaded (offline, gated repo not accepted, transformers not installed).
#
# It used to be a guessed 3.2 chars/token, derived from a generic
# words/chars ratio. Measured directly against the real Gemma 4 tokenizer
# over an 80-chunk random sample of this corpus, Bahdini Arabic-script text
# actually comes out to ~1.6 chars/token -- it fragments much more than that
# generic guess assumed, roughly 2x as many tokens for the same text. Keep
# this close to that measurement; re-measure (see qa_generation/README.md)
# if the tokenizer model changes.
CHARS_PER_TOKEN = 1.6

# HF repo for the real tokenizer (no model weights needed, just the
# tokenizer files -- see gemma_tokenizer.py). Verify this id still points at
# the intended checkpoint if it's ever re-downloaded.
GEMMA_TOKENIZER_MODEL = "google/gemma-4-31B-it"


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


# --- chunking -----------------------------------------------------------
#
# Confirmed with the partner: the ~1,000-token budget covers the prompt side
# only (system + question + context), not the answer, and it's a mean, not
# a hard cap, so going over it in some cases is fine. Measured with the real
# Gemma 4 chat template (system prompt 13 tokens; a representative ~40-token
# Bahdini question; rendered with add_generation_prompt=True to match the
# actual inference-time prompt) came to 77 fixed tokens for that example, so
# ~900 tokens of context leaves comfortable room under the 1,000-token mean
# for typical questions, with MAX_CHUNK_TOKENS as headroom for when a
# question runs long.
TARGET_CHUNK_TOKENS = 900
MAX_CHUNK_TOKENS = 1050
MIN_CHUNK_TOKENS = 120  # below this a chunk is too thin to ground a QA pair

# Fraction of finished QA pairs delivered WITH context in the user message;
# the rest are delivered as a bare question (no "Context: ..." block), per
# the partner's two serving modes (retrieval-augmented vs. not). Applied at
# compile time in compile_qa_dataset.py, not during generation -- Gemini
# always sees the full context so every pair stays grounded either way.
CONTEXT_RATIO = 0.7

# --- source selection -----------------------------------------------------
#
# Both source pools are trusted and included unconditionally:
# extractions/*/safe/ (native PDF text layer, classified "safe" by
# scripts/extract_pipeline.py) and gemini_ocr_pipeline/output/corpus/
# (Gemini OCR output, classification == "kurdish"; this corpus has been
# reviewed and accepted, see that pipeline's README/guide).

# --- QA generation prompt (Gemini) ---------------------------------------
#
# Fed to Gemini once per chunk. Produces the *content* of each QA pair only
# (question/answer/question_type); compile_qa_dataset.py wraps that into the
# agreed messages+metadata JSONL schema, so the model never has to know
# about that envelope.
QUESTION_TYPES = ["factual", "explanatory", "summarization", "definitional", "inferential"]

QA_PROMPT_VERSION = "v2"
QA_SYSTEM_PROMPT = "Answer the question in Bahdini Kurdish using the supplied context."
# Used instead of QA_SYSTEM_PROMPT for the CONTEXT_RATIO-share of records
# delivered without a context block, since "using the supplied context"
# would be wrong when there isn't one. Not something the partner specified
# in words; a direct consequence of the with/without-context split they did
# ask for. Adjust the wording here if they want something different.
QA_SYSTEM_PROMPT_NO_CONTEXT = "Answer the question in Bahdini Kurdish."

PAIRS_PER_CHUNK = 3

QA_GENERATION_PROMPT_TEMPLATE = (
    "You are building instruction-tuning data for a Bahdini (Badini) Kurdish "
    "language model. Bahdini is the Kurmanji dialect written in Arabic "
    "script, spoken in the Duhok/Badinan region.\n"
    "\n"
    "Below is one excerpt (\"the context\") from a Bahdini text. Write "
    "{n_pairs} question-answer pairs a person could ask about this excerpt, "
    "as if using it for reading comprehension / instruction fine-tuning.\n"
    "\n"
    "Rules:\n"
    "- Both the question and the answer must be written in Bahdini Kurdish, "
    "in the same orthography as the context (do not switch to Sorani, "
    "Latin-script Kurdish, or Arabic).\n"
    "- The answer must be grounded in the context: prefer extractive answers "
    "quoting or closely paraphrasing the text; reasonable inference/synthesis "
    "is fine for explanatory or summarization questions, but never introduce "
    "facts that are not supported by the context.\n"
    "- Do not ask about the excerpt's formatting, page numbers, or the fact "
    "that it is an excerpt.\n"
    "- Vary the question_type across the set; choose only from: "
    f"{', '.join(QUESTION_TYPES)}.\n"
    "- If the context is too short, garbled, or content-free to support "
    "{n_pairs} distinct, well-grounded questions, return fewer pairs (an "
    "empty list is fine) rather than inventing filler.\n"
    "- If answering the question genuinely requires connecting multiple "
    "details in the context or drawing a conclusion beyond a single stated "
    "fact, also fill in \"reasoning\": a brief step-by-step justification, "
    "in Bahdini Kurdish, for how the answer follows from the context. If "
    "the question is directly answerable by quoting or restating one "
    "explicit fact, set \"reasoning\" to null; do not pad it with filler.\n"
    "\n"
    "Output strict JSON only: a list of objects, no markdown fences, no "
    "commentary before or after. Each object must have exactly these keys:\n"
    "  \"question\": string\n"
    "  \"answer\": string\n"
    "  \"question_type\": one of {question_types}\n"
    "  \"reasoning\": string or null (see rule above)\n"
    "\n"
    "Context:\n"
    "\"\"\"\n"
    "{context}\n"
    "\"\"\"\n"
)


def build_qa_prompt(context: str, n_pairs: int = PAIRS_PER_CHUNK) -> str:
    return QA_GENERATION_PROMPT_TEMPLATE.format(
        n_pairs=n_pairs, context=context, question_types=QUESTION_TYPES,
    )


# --- OpenRouter backend ---------------------------------------------------
#
# Reuses the OPENROUTER_API_KEY already configured for gemini_ocr_pipeline/.
# Pick a stronger Gemini tier than the OCR pipeline's flash-lite (QA
# generation needs better instruction-following / reasoning in Bahdini);
# verify this slug against the current OpenRouter model catalog before
# running at scale, and adjust pricing below to match.
OPENROUTER_MODEL = "google/gemini-3.1-pro-preview"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# PLACEHOLDER pricing -- verify against OpenRouter's model page before
# trusting any cost total computed with these.
OPENROUTER_INPUT_USD_PER_M = 1.25
OPENROUTER_OUTPUT_USD_PER_M = 5.00

MAX_OUTPUT_TOKENS = 2048


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * OPENROUTER_INPUT_USD_PER_M
        + output_tokens * OPENROUTER_OUTPUT_USD_PER_M
    ) / 1_000_000


def doc_id(source: str, input_path: str) -> str:
    """Stable short id for one source document; same scheme as
    gemini_ocr_pipeline/ocr_config.doc_id so ids line up across pipelines
    for documents that exist in both (native-safe vs. OCR'd)."""
    import hashlib

    return hashlib.sha1(f"{source}/{input_path}".encode("utf-8")).hexdigest()[:16]


def load_dotenv_key(name: str, env_path: Path = ROOT / ".env") -> str:
    """Minimal .env reader so the OpenRouter key doesn't need to be exported."""
    import os

    value = os.environ.get(name)
    if value:
        return value
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{name} not set in the environment or in {env_path}")
