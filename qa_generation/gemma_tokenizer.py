"""Real token counts from the target fine-tuning tokenizer (google/gemma-4-31B-it).

build_chunks.py and compile_qa_dataset.py both need "how many tokens is this,
really" -- for the Gemma 4 31B IT LoRA fine-tune the answer depends on the
Gemma tokenizer, which fragments Arabic-script Kurdish quite differently from
the character-based guess qa_config.CHARS_PER_TOKEN used to make (measured at
~1.6 chars/token on this corpus, not the ~3.2 originally assumed -- see
qa_generation/README.md for the measurement).

The tokenizer is loaded lazily and only once per process. If it can't be
loaded (offline, not yet downloaded, gated repo not accepted, transformers
not installed), every function below transparently falls back to the
char-based estimate in qa_config so the pipeline still runs -- just with the
previously-understood, correspondingly reduced precision.
"""

import sys

import qa_config as cfg

_tokenizer = None
_load_failed = False
_warned = False


def _warn_fallback(exc=None):
    global _warned
    if _warned:
        return
    _warned = True
    reason = f" ({exc})" if exc else ""
    print(f"  (real tokenizer '{cfg.GEMMA_TOKENIZER_MODEL}' unavailable{reason}; falling "
          f"back to the char-based estimate, {cfg.CHARS_PER_TOKEN} chars/token)",
          file=sys.stderr)


def _load():
    global _tokenizer, _load_failed
    if _tokenizer is not None or _load_failed:
        return _tokenizer
    try:
        from transformers import AutoTokenizer
        _tokenizer = AutoTokenizer.from_pretrained(cfg.GEMMA_TOKENIZER_MODEL)
    except Exception as exc:  # noqa: BLE001 -- any load failure just means "use the fallback"
        _load_failed = True
        _warn_fallback(exc)
    return _tokenizer


def available() -> bool:
    return _load() is not None


def raw():
    """The underlying transformers tokenizer, or None if unavailable."""
    return _load()


def count_tokens(text: str) -> int:
    tok = _load()
    if tok is None:
        return cfg.estimate_tokens(text)
    return len(tok(text, add_special_tokens=False)["input_ids"])


def count_tokens_batch(texts: list) -> list:
    """Batched version of count_tokens -- one call to the fast tokenizer
    instead of one Python-level call per string; use this in hot loops."""
    if not texts:
        return []
    tok = _load()
    if tok is None:
        return [cfg.estimate_tokens(text) for text in texts]
    return [len(ids) for ids in tok(texts, add_special_tokens=False)["input_ids"]]


def count_chat_tokens(messages: list) -> int:
    """Token count of a full messages=[...] record as it will actually be
    fed to the model: chat-template-rendered (BOS + turn markers included),
    not just the sum of each message's raw content."""
    tok = _load()
    if tok is None:
        return sum(cfg.estimate_tokens(m["content"]) for m in messages)
    rendered = tok.apply_chat_template(messages, tokenize=False)
    return len(tok(rendered, add_special_tokens=False)["input_ids"])


def count_prompt_tokens(messages: list) -> int:
    """Token count of the prompt Gemma actually sees before generating the
    answer: every message up to (not including) the assistant turn,
    rendered with add_generation_prompt=True. This is what the partner's
    ~1,000-token budget is measured against -- the answer is not counted."""
    tok = _load()
    prompt_messages = [m for m in messages if m["role"] != "assistant"]
    if tok is None:
        return sum(cfg.estimate_tokens(m["content"]) for m in prompt_messages)
    rendered = tok.apply_chat_template(
        prompt_messages, tokenize=False, add_generation_prompt=True)
    return len(tok(rendered, add_special_tokens=False)["input_ids"])


def encode(text: str) -> list:
    tok = _load()
    if tok is None:
        return None
    return tok(text, add_special_tokens=False)["input_ids"]


def decode(ids: list) -> str:
    tok = _load()
    return tok.decode(ids)
