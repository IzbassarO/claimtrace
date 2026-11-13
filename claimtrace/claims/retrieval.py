"""Pick the source passages most relevant to a citing sentence: BM25 + dense, fused by RRF."""

import asyncio
import hashlib
import re
import threading
from array import array
from collections import OrderedDict
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
_EMBED_BATCH = 16
_CACHE_TEXTS = 64
# (embed fn, sha256 of source text) -> passage vectors; float32 arrays keep it small.
_chunk_vecs: OrderedDict[tuple[object, str], list[array]] = OrderedDict()


def fastembed_embed(texts: list[str]) -> list[list[float]]:
    """Default embedder (blocking; call in a thread). Loads the model once, lazily.
    One embedding at a time with small batches: concurrent ONNX runs with the default batch of
    256 grew the container past 5 GB."""
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding

            _model = TextEmbedding("BAAI/bge-small-en-v1.5", threads=2)
        return [v.tolist() for v in _model.embed(texts, batch_size=_EMBED_BATCH)]


async def _passage_vectors(embed: Embed, text: str, chunks: list[str]) -> list[array]:
    """Each source's passages are embedded once, however many claims cite it."""
    key = (embed, hashlib.sha256(text.encode()).hexdigest())
    if key in _chunk_vecs:
        _chunk_vecs.move_to_end(key)
        return _chunk_vecs[key]
    vecs = [array("f", v) for v in await asyncio.to_thread(embed, chunks)]
    if len(vecs) != len(chunks):
        raise ValueError("embedder returned the wrong number of vectors")
    _chunk_vecs[key] = vecs
    while len(_chunk_vecs) > _CACHE_TEXTS:
        _chunk_vecs.popitem(last=False)
    return vecs


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
    corpus = [_tokens(c) for c in chunks]
    if not any(corpus):  # no word characters at all: BM25 divides by zero, nothing to rank
        return chunks[:TOP_K]
    rankings = [_ranks(BM25Okapi(corpus).get_scores(_tokens(query)))]
    try:
        embed = embed or fastembed_embed
        chunk_vecs = await _passage_vectors(embed, text, chunks)
        (query_vec,) = await asyncio.to_thread(embed, [query])
        rankings.append(_ranks(_cosine_scores([query_vec, *chunk_vecs])))
    except Exception:  # embedding is a bonus: BM25 alone still works
        pass
    fused = [0.0] * len(chunks)
    for ranking in rankings:
        for rank, i in enumerate(ranking):
            fused[i] += 1.0 / (_RRF_K + rank + 1)
    return [chunks[i] for i in sorted(_ranks(fused)[:TOP_K])]
