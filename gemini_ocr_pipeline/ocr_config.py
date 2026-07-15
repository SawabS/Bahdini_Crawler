"""Shared configuration for the Gemini OCR pipeline.

Everything that affects the transcription result (model, prompt, rendering)
is versioned here so every page record can state exactly how it was produced.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "gemini_ocr_pipeline" / "output"
MANIFEST_PATH = OUTPUT_DIR / "manifest.jsonl"
PAGES_DIR = OUTPUT_DIR / "pages"
CORPUS_DIR = OUTPUT_DIR / "corpus_unreviewed"

PROJECT = "bahdini-data"
VERTEX_LOCATION = "global"
GEMINI_MODEL = "gemini-3.1-flash-lite"

# Rendering: ~288 DPI grayscale PNG, matching the completed A/B pilot, but
# capped so oversized scans do not produce needlessly huge images.
RENDER_ZOOM = 4.0
MAX_LONG_SIDE_PX = 3400

# Estimated Vertex AI pricing in USD per million tokens. These reproduce the
# pilot's ~$0.0099 for 12,628 input + 4,498 output tokens; verify against the
# current Vertex AI price list before trusting absolute numbers.
INPUT_USD_PER_M = 0.30
OUTPUT_USD_PER_M = 1.35

MAX_OUTPUT_TOKENS = 8192

NO_TEXT_MARKER = "[NO_TEXT]"
NOT_BADINI_PREFIX = "[NOT_BADINI"
UNCLEAR_MARKER = "[unclear]"

PROMPT_VERSION = "v4"
PROMPT = (
    "You are building a Bahdini (Badini) Kurdish text corpus from one scanned "
    "page of a printed book or magazine. Bahdini is the Kurmanji dialect "
    "written in Arabic script, as used in the Duhok/Badinan region.\n"
    "\n"
    "First, silently judge the dominant language of the page's body text:\n"
    "- If the main text is Bahdini Kurdish, transcribe it following the rules "
    "below.\n"
    "- If the main text is another language - Arabic, Sorani Kurdish, "
    "Latin-script Kurmanji, Persian, Turkish, or English - do NOT transcribe "
    "anything. Return exactly [NOT_BADINI: language] naming that language, "
    "and nothing else.\n"
    "- If the page is mostly Bahdini with short passages in other languages "
    "(quotes, verses, titles), transcribe the whole page including those "
    "passages.\n"
    "- Hints: Bahdini uses forms like \"ژ\", \"ب\", \"د ... دا\", \"ئەڤ\", "
    "\"هندەک\", \"دێ\" + verb, and \"ڤ\" is common. Sorani instead uses "
    "\"لە\", \"ئەم\", \"دە-\" verb prefixes like \"دەکات\", and \"ـەوە\" "
    "endings.\n"
    "\n"
    "Transcription rules:\n"
    "- Transcribe exactly what is printed, character by character, in the "
    "original script. Do not translate, normalize spelling, modernize, "
    "correct grammar, or fill in missing words.\n"
    "- The page may contain passages in Arabic, Persian, Turkish, or English; "
    "transcribe those exactly as written too.\n"
    "- Follow the natural reading order (right-to-left for Arabic script). "
    "For multi-column layouts, transcribe one column completely before "
    "moving to the next.\n"
    "- Preserve paragraph breaks and poetry verse line breaks. Never merge "
    "separate verse lines into one line.\n"
    "- Skip page numbers, running headers and footers repeated from page to "
    "page, website watermarks, and purely decorative text.\n"
    "- Be exact with visually similar letters; read the dots carefully at "
    "high zoom. Especially: ڤ (THREE dots above) vs ق (two dots above) vs "
    "ف (one dot); پ (three dots below) vs ب (one dot below); چ vs ج; گ vs "
    "ک; ژ vs ز; ێ vs ی; ۆ vs و. Bahdini uses ڤ very frequently (ئاڤ, ناڤ, "
    "هەڤ, دڤێت); when a word could read as either ڤ or ق, look again at the "
    "dot count instead of defaulting to the Arabic letter.\n"
    "- Write [unclear] in place of a word or character that is genuinely "
    "unreadable. Never guess or invent text.\n"
    "- If the page has no transcribable body text at all (blank page, pure "
    "image or cover art), return exactly [NO_TEXT].\n"
    "- Output plain text only: no markdown, no code fences, no commentary, "
    "no image descriptions."
)


def doc_id(source: str, input_path: str) -> str:
    """Stable short id for one source document, used to key page records."""
    import hashlib

    return hashlib.sha1(f"{source}/{input_path}".encode("utf-8")).hexdigest()[:16]


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * INPUT_USD_PER_M + output_tokens * OUTPUT_USD_PER_M
    ) / 1_000_000
