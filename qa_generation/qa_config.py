"""Shared configuration for the QA-pair generation pipeline.

Turns the clean Bahdini text corpus (native "safe" extractions, plus
human-reviewed Gemini OCR output once review happens) into context chunks,
then into instruction-tuning QA pairs for the Gemma 4 31B IT LoRA fine-tune.
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
OCR_CORPUS_DIR = ROOT / "gemini_ocr_pipeline" / "output" / "corpus_unreviewed"
OCR_CORPUS_JSONL = OCR_CORPUS_DIR / "corpus.jsonl"

# --- token estimation -------------------------------------------------
#
# No tokenizer is wired in here, so tokens are estimated from characters.
# docs/CRAWL_REPORT.md's own numbers for the clean Bahdini pool (~43.8M
# chars / ~8.6M words -> ~5.1 chars/word) combined with scripts/
# token_estimate.py's rule of thumb for this corpus (~1.6 tokens/word for
# Arabic-script Kurdish, which fragments more than Latin script under
# byte-level BPE) give ~3.2 chars/token. Good enough for chunk sizing and
# sanity-checking record length; re-derive from an actual Gemma/Gemini
# tokenizer before trusting it for anything cost-critical.
CHARS_PER_TOKEN = 3.2


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


# --- chunking -----------------------------------------------------------
#
# The partner side estimated ~1,000 tokens per finished QA record
# (system + context + question + answer combined -- see the email thread).
# Budget: ~20 tokens system, ~40 question, ~150 answer, leaving the context
# chunk itself a target of ~700 tokens with headroom up to 850.
TARGET_CHUNK_TOKENS = 700
MAX_CHUNK_TOKENS = 850
MIN_CHUNK_TOKENS = 120  # below this a chunk is too thin to ground a QA pair

# --- source selection -----------------------------------------------------
#
# Default pool: only extractions/*/safe/ (native PDF text layer, already
# classified "safe" by scripts/extract_pipeline.py, no review gate).
# gemini_ocr_pipeline/output/corpus_unreviewed/ is explicitly NOT promoted
# to training data automatically (see that pipeline's README/guide) -- it
# is included here only for documents with review_status == "reviewed",
# unless --include-unreviewed-ocr opts into the unreviewed pool for an
# early sample, which build_chunks.py flags loudly when used.
OCR_MIN_COMPLETENESS_IF_UNREVIEWED = 0.98

# --- QA generation prompt (Gemini) ---------------------------------------
#
# Fed to Gemini once per chunk. Produces the *content* of each QA pair only
# (question/answer/question_type); compile_qa_dataset.py wraps that into the
# agreed messages+metadata JSONL schema, so the model never has to know
# about that envelope.
QUESTION_TYPES = ["factual", "explanatory", "summarization", "definitional", "inferential"]

QA_PROMPT_VERSION = "v1"
QA_SYSTEM_PROMPT = "Answer the question in Bahdini Kurdish using the supplied context."

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
    "\n"
    "Output strict JSON only: a list of objects, no markdown fences, no "
    "commentary before or after. Each object must have exactly these keys:\n"
    "  \"question\": string\n"
    "  \"answer\": string\n"
    "  \"question_type\": one of {question_types}\n"
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
