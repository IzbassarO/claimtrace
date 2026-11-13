"""Pick the source passages most relevant to a citing sentence: BM25 over all passages, then a
dense rerank of the BM25 top candidates, fused by RRF; a cross-encoder reorders the fused top.
Passages follow paragraphs and section headings; section_of() labels a verified quote."""

import asyncio
import re
import threading
from collections.abc import Callable, Sequence

from rank_bm25 import BM25Okapi

from claimtrace.claims.quote import verify_quote

Embed = Callable[[list[str]], Sequence[Sequence[float]]]
Rerank = Callable[[str, list[str]], Sequence[float]]  # (query, passages) -> one score each

WHOLE_TEXT_CHARS = 2500
TOP_K = 6
CANDIDATES = 40  # BM25 top passages that get embedded; embedding all of them is CPU-bound
RERANK_POOL = 12  # fused top passages the cross-encoder scores
_RRF_K = 60
_SENT = re.compile(r"(?<=[.!?])\s+")
_TOKEN = re.compile(r"\w+")
_MAX_SENT_WORDS = 120
_BLOCK = re.compile(r"\n[ \t\r\f\v]*\n\s*")
HEADING_WORDS = 12
_FLOAT_LABEL = re.compile(r"(?i)(fig(ure)?\.?|table|box|scheme)\s*[a-z]?\d")

_lock = threading.Lock()
_model = None
_reranker = None
_EMBED_BATCH = 16


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


def fastembed_rerank(query: str, docs: list[str]) -> list[float]:
    """Default cross-encoder (blocking; call in a thread). Loads once, lazily, under the same lock
    as the embedder so the two ONNX models never run concurrently."""
    global _reranker
    with _lock:
        if _reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            _reranker = TextCrossEncoder("Xenova/ms-marco-MiniLM-L-6-v2", threads=2)
        return [float(x) for x in _reranker.rerank(query, docs, batch_size=_EMBED_BATCH)]


def _sentences(text: str) -> list[str]:
    """Over-long 'sentences' (tables, PDF noise) are cut into word chunks so one passage cannot
    swallow the prompt."""
    out = []
    for s in _SENT.split(text.strip()):
        words = s.split()
        out += [
            " ".join(words[i : i + _MAX_SENT_WORDS]) for i in range(0, len(words), _MAX_SENT_WORDS)
        ]
    return out


def _is_heading(block: str) -> bool:
    """'Introduction', '2 Methods', '4.1 Training data': one short line, no final period."""
    words = block.split()
    return (
        "\n" not in block
        and 0 < len(words) <= HEADING_WORDS
        and "|" not in block  # a JATS table row
        and block[-1] not in ".,;:"
        and (block[0].isupper() or block[0].isdigit())
        and any(c.isalpha() for c in block)
    )


def paragraphs(text: str) -> list[tuple[str | None, str]]:
    """(section heading or None, paragraph) per blank-line separated block; headings are not
    paragraphs. Never guesses: short lines before the first real paragraph are front matter
    (title, authors), and after a 'Figure 2' / 'Table 1' label the section is unknown (None)
    until the next heading, so caption titles and text after an inline float get no label."""
    out: list[tuple[str | None, str]] = []
    section, seen_para, in_float = None, False, False
    for block in (b.strip() for b in _BLOCK.split(text)):
        if not block:
            continue
        heading = _is_heading(block)
        if seen_para and heading and _FLOAT_LABEL.match(block):
            section, in_float = None, True
        elif seen_para and heading and not in_float:
            section = block
            continue
        elif not heading:
            seen_para, in_float = True, False
        out.append((section, block))
    return out


def passages(text: str) -> list[str]:
    """~3-sentence windows overlapping by one sentence, inside a paragraph; a paragraph's first
    window also starts with the previous paragraph's last sentence when both are in the same
    section. Section headings themselves are not passage text."""
    out, prev = [], (None, None)  # (section, last sentence) of the previous paragraph
    for section, para in paragraphs(text):
        sents = _sentences(para)
        if not sents:
            continue
        carry = [prev[1]] if prev[1] is not None and prev[0] == section else []
        prev = (section, sents[-1])
        sents = carry + sents
        if len(sents) <= 3:
            out.append(" ".join(sents))
        else:
            out += [" ".join(sents[i : i + 3]) for i in range(0, len(sents) - 1, 2)]
    return out


def section_of(quote: str, text: str) -> str | None:
    """Heading of the section whose paragraph contains the verified quote verbatim; None when
    no single paragraph has it or paragraphs in different sections do (never guess)."""
    found = {section for section, para in paragraphs(text) if verify_quote(quote, para)}
    return found.pop() if len(found) == 1 else None


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


async def retrieve(
    query: str, text: str, embed: Embed | None = None, rerank: Rerank | None = None
) -> list[str]:
    """Short texts are returned whole; otherwise the top passages in document order."""
    if len(text) < WHOLE_TEXT_CHARS:
        return [text]
    chunks = passages(text)
    if len(chunks) <= TOP_K:
        return chunks
    corpus = [_tokens(c) for c in chunks]
    if not any(corpus):  # no word characters at all: BM25 divides by zero, nothing to rank
        return chunks[:TOP_K]
    bm25 = _ranks(BM25Okapi(corpus).get_scores(_tokens(query)))[:CANDIDATES]
    rankings = [bm25]
    try:
        texts = [query, *(chunks[i] for i in bm25)]
        vecs = await asyncio.to_thread(embed or fastembed_embed, texts)
        if len(vecs) != 1 + len(bm25):
            raise ValueError("embedder returned the wrong number of vectors")
        rankings.append([bm25[r] for r in _ranks(_cosine_scores(vecs))])
    except Exception:  # embedding is a bonus: BM25 alone still works
        pass
    fused = dict.fromkeys(bm25, 0.0)
    for ranking in rankings:
        for rank, i in enumerate(ranking):
            fused[i] += 1.0 / (_RRF_K + rank + 1)
    order = sorted(fused, key=lambda i: -fused[i])
    pool = order[:RERANK_POOL]
    try:
        scores = await asyncio.to_thread(
            rerank or fastembed_rerank, query, [chunks[i] for i in pool]
        )
        if len(scores) != len(pool):
            raise ValueError("reranker returned the wrong number of scores")
        order = [pool[r] for r in _ranks(scores)]
    except Exception:  # the reranker is a bonus: keep the hybrid order
        pass
    return [chunks[i] for i in sorted(order[:TOP_K])]
