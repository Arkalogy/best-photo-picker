"""CLIP BPE tokenizer — pure Python, no external dependencies.

Based on OpenAI's CLIP tokenizer (simple_tokenizer.py). Downloads the BPE
vocabulary file on first use to ``~/.cache/bpp/bpe_simple_vocab_16e6.txt.gz``.

Thread safety: the singleton vocabulary is loaded once and is read-only afterwards.
"""

from __future__ import annotations

import gzip
import html
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from bpp.scoring.model_base import ModelSingleton
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# Special token IDs
SOT_TOKEN = 49406  # <|startoftext|>
EOT_TOKEN = 49407  # <|endoftext|>
CONTEXT_LENGTH = 77

from bpp.utils.paths import cache_dir as _cache_dir  # noqa: E402

_VOCAB_DIR = _cache_dir()

# ── Vocabulary: CLIP byte-level BPE (49,408 tokens) ─────────────────
# What:   the byte-pair encoding vocab paired with OpenAI's CLIP
#         text encoder. Tokenizes free-text queries before they hit
#         the text-encoder ONNX graph in clip_embed.py.
# Where:  openaipublic.azureedge.net is OpenAI's official CDN for
#         CLIP artifacts (referenced in their published Python SDK).
# Why this one: the vocab MUST match the text encoder it's paired
#         with — this is the file the openai/CLIP-ViT-B/32 model in
#         clip_embed.py was trained against. A mismatched vocab
#         silently produces wrong embeddings.
# License: MIT (matches the CLIP model release).
# To bump: only when bumping the CLIP text encoder in clip_embed.py
#         to a different OpenAI release.
_VOCAB_FILENAME = "bpe_simple_vocab_16e6.txt.gz"
_VOCAB_URL = "https://openaipublic.azureedge.net/clip/bpe_simple_vocab_16e6.txt.gz"
_VOCAB_SHA256 = "924691ac288e54409236115652ad4aa250f48203de50a9e4722a6ecd48d6804a"

# Regex to split text into tokens (ASCII-friendly, no `regex` dependency)
_PAT = re.compile(
    r"""'s|'t|'re|'ve|'m|'ll|'d|"""
    r"""[a-zA-Z]+|"""
    r"""[0-9]|"""
    r"""[^\sa-zA-Z0-9]+""",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Byte-level BPE helpers
# ---------------------------------------------------------------------------


@lru_cache
def _bytes_to_unicode() -> dict[int, str]:
    """Map byte values to unicode characters for BPE.

    Printable ASCII/Latin-1 chars map to themselves; others get offset
    to avoid whitespace/control characters that break tokenization.
    """
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("\xa1"), ord("\xac") + 1))
        + list(range(ord("\xae"), ord("\xff") + 1))
    )
    cs = list(bs)
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, (chr(c) for c in cs), strict=True))


def _get_pairs(word: tuple[str, ...]) -> set[tuple[str, str]]:
    """Get all adjacent character pairs in a word."""
    return {(word[i], word[i + 1]) for i in range(len(word) - 1)}


# ---------------------------------------------------------------------------
# Vocabulary loading
# ---------------------------------------------------------------------------


def _build_vocab(
    path: Path | None,
) -> tuple[dict[tuple[str, str], int], dict[str, int], dict[int, str]]:
    """Load BPE merge rules from *path* and build encoder/decoder."""
    assert path is not None, "Vocab path must not be None"
    with gzip.open(str(path), "rt", encoding="utf-8") as f:
        merges = f.read().strip().split("\n")
    # First line is header: "#version: ..."
    merges = merges[1:]
    merges = [tuple(m.split()) for m in merges if len(m.split()) == 2]

    byte_enc = _bytes_to_unicode()
    vocab = list(byte_enc.values())
    vocab += [v + "</w>" for v in vocab]
    for merge in merges:
        vocab.append("".join(merge))
    vocab.extend(["<|startoftext|>", "<|endoftext|>"])

    encoder = {token: i for i, token in enumerate(vocab)}
    decoder = {i: token for token, i in encoder.items()}
    bpe_ranks = {pair: i for i, pair in enumerate(merges)}

    return bpe_ranks, encoder, decoder


_vocab = ModelSingleton(
    name="CLIP vocabulary",
    model_path=_VOCAB_DIR / _VOCAB_FILENAME,
    model_url=_VOCAB_URL,
    model_sha256=_VOCAB_SHA256,
    create_fn=_build_vocab,
    registry_id="openai_clip_vit_b32_onnx",
)


def _load_vocab() -> tuple[dict[tuple[str, str], int], dict[str, int], dict[int, str]]:
    """Load BPE merge rules and build encoder/decoder (thread-safe singleton)."""
    result = _vocab.get()
    if result is None:
        raise RuntimeError("CLIP BPE vocabulary unavailable — cannot tokenize text")
    return result


# ---------------------------------------------------------------------------
# BPE encoding
# ---------------------------------------------------------------------------


# Exposed for the /api/v1/debug/memory endpoint — readable as len(_bpe_cache).
# lru_cache caps growth at maxsize (CLIP vocab is ~49k tokens; typical search
# sessions touch a few hundred distinct word tokens, so 4096 is generous).
@lru_cache(maxsize=4096)
def _bpe(token: str) -> str:
    """Apply BPE to a single word token."""
    bpe_ranks, _, _ = _load_vocab()

    word = (*tuple(token[:-1]), token[-1] + "</w>")
    pairs = _get_pairs(word)
    if not pairs:
        return token + "</w>"

    while True:
        # Find the highest-priority (lowest rank) merge pair
        bigram = min(pairs, key=lambda p: bpe_ranks.get(p, float("inf")))
        if bigram not in bpe_ranks:
            break
        first, second = bigram
        new_word: list[str] = []
        i = 0
        while i < len(word):
            try:
                j = word.index(first, i)
            except ValueError:
                new_word.extend(word[i:])
                break
            new_word.extend(word[i:j])
            if j < len(word) - 1 and word[j + 1] == second:
                new_word.append(first + second)
                i = j + 2
            else:
                new_word.append(word[j])
                i = j + 1
        word = tuple(new_word)
        if len(word) == 1:
            break
        pairs = _get_pairs(word)

    return " ".join(word)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _clean_text(text: str) -> str:
    """Basic text cleaning (CLIP-style)."""
    text = html.unescape(text)
    text = text.strip()
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def tokenize(text: str, context_length: int = CONTEXT_LENGTH) -> np.ndarray:
    """Tokenize a text string into CLIP token IDs.

    Returns int32 array of shape (1, context_length).
    """
    _, encoder, _ = _load_vocab()
    byte_enc = _bytes_to_unicode()

    text = _clean_text(text)
    tokens = [SOT_TOKEN]

    for match in _PAT.findall(text):
        # Encode each byte of the word to the BPE unicode representation
        encoded = "".join(byte_enc[b] for b in match.encode("utf-8"))
        bpe_tokens = _bpe(encoded).split(" ")
        tokens.extend(encoder[t] for t in bpe_tokens)

    tokens.append(EOT_TOKEN)

    # Truncate to context_length (keeping SOT at 0 and EOT at end)
    if len(tokens) > context_length:
        tokens = [*tokens[: context_length - 1], EOT_TOKEN]

    # Pad to context_length
    result = np.zeros((1, context_length), dtype=np.int32)
    result[0, : len(tokens)] = tokens
    return result


# ── Registry ───────────────────────────────────────────────────────
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="CLIP vocabulary",
        path=str(_VOCAB_DIR / _VOCAB_FILENAME),
        url=_VOCAB_URL,
        sha256=_VOCAB_SHA256,
        reset=_vocab.reset,
    )
)
