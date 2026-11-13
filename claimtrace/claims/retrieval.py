"""Pick the source passages most relevant to a citing sentence: BM25 + dense, fused by RRF."""

import asyncio
import re
import threading
from collections.abc import Callable, Sequence

from rank_bm25 import BM25Okapi

Embed = Callable[[list[str]], Sequence[Sequence[float]]]

WHOLE_TEXT_CHARS = 2500
TOP_K = 6
_RRF_K = 60
_SENT = re.compile(r"(?<=[.!?])\s+")
_TOKEN = re.compile(r"\w+")
_MAX_SENT_WORDS = 120

_lock = threading.Lock()
_model = None


def fastembed_embed(texts: list[str]) -> list[list[float]]:
    """Default embedder (blocking; call in a thread). Loads the model once, lazily."""
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding

            _model = TextEmbedding("BAAI/bge-small-en-v1.5")
    return [list(v) for v in _model.embed(texts)]


def passages(text: str) -> list[str]:
    """~3-sentence windows overlapping by one sentence. Over-long 'sentences' (tables, PDF noise)
    are cut into word chunks so one passage cannot swallow the prompt."""
    sents = []
    for s in _SENT.split(text.strip()):
        words = s.split()
        sents += [
            " ".join(words[i : i + _MAX_SENT_WORDS]) for i in range(0, len(words), _MAX_SENT_WORDS)
        ]
    if len(sents) <= 3:
        return [" ".join(sents)] if sents else []
    return [" ".join(sents[i : i + 3]) for i in range(0, len(sents) - 1, 2)]


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _ranks(scores: Sequence[float]) -> list[int]:
    return sorted(range(len(scores)), key=lambda i: -scores[i])


def _cosine_scores(vecs: Sequence[Sequence[float]]) -> list[float]:
    q, docs = vecs[0], vecs[1:]

    def norm(v):
        return sum(x * x for x in v) ** 0.5 or 1.0

    qn = norm(q)
    return [sum(a * b for a, b in zip(q, d, strict=True)) / (qn * norm(d)) for d in docs]


async def retrieve(query: str, text: str, embed: Embed | None = None) -> list[str]:
    """Short texts are returned whole; otherwise the top passages in document order."""
    if len(text) < WHOLE_TEXT_CHARS:
        return [text]
    chunks = passages(text)
    if len(chunks) <= TOP_K:
        return chunks
    rankings = [_ranks(BM25Okapi([_tokens(c) for c in chunks]).get_scores(_tokens(query)))]
    try:
        vecs = await asyncio.to_thread(embed or fastembed_embed, [query, *chunks])
        rankings.append(_ranks(_cosine_scores(vecs)))
    except Exception:  # embedding is a bonus: BM25 alone still works
        pass
    fused = [0.0] * len(chunks)
    for ranking in rankings:
        for rank, i in enumerate(ranking):
            fused[i] += 1.0 / (_RRF_K + rank + 1)
    return [chunks[i] for i in sorted(_ranks(fused)[:TOP_K])]
