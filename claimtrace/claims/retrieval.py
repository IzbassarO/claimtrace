"""Pick the source passages most relevant to a citing sentence: BM25 over all passages, then a
dense rerank of the BM25 top candidates, fused by RRF; a cross-encoder reorders the fused top.
section_of() labels a verified quote with its source section (not used for retrieval)."""

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
_SECTION = re.compile(
    r"(?i)(?:(?:\d+(?:\.\d+)*|[ivx]+)\.?\s+)?(introduction|background|related work|methods"
    r"|materials and methods|methodology|experimental setup|experiments|data|results"
    r"|results and discussion|evaluation|analysis|discussion|conclusions?|limitations"
    r"|future work|supplementary)"
)
# 'Figure 2.', 'Fig. 2', 'Table II', 'Table 1: ...'; not 'Table 1 shows ...' (body text)
_FLOAT_LABEL = re.compile(
    r"(?i)(?:fig(?:ure)?\.?|table|box|scheme)\s*(?:[a-z]?\d+[a-z]?|[ivxlc]+)\b\s*(?:$|[.:])"
)

_lock = threading.Lock()  # lazy model loading only
# ponytail: 2 concurrent inferences (ONNX `run` is thread-safe); each ONNX session uses 2 threads
# and batch 16, so memory stays bounded. Raise with care: the container is capped at 1.5 GB.
_infer = threading.BoundedSemaphore(2)
_model = None
_reranker = None
_EMBED_BATCH = 16


def fastembed_embed(texts: list[str]) -> list[list[float]]:
    """Default embedder (blocking; call in a thread). Loads the model once, lazily.
    At most 2 inferences at a time with small batches: unbounded concurrent ONNX runs with the
    default batch of 256 grew the container past 5 GB."""
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding

            _model = TextEmbedding("BAAI/bge-small-en-v1.5", threads=2)
    with _infer:
        return [v.tolist() for v in _model.embed(texts, batch_size=_EMBED_BATCH)]


def fastembed_rerank(query: str, docs: list[str]) -> list[float]:
    """Default cross-encoder (blocking; call in a thread). Loads once, lazily; inference shares
    the embedder's 2-slot semaphore, so at most 2 ONNX runs (of either model) at a time."""
    global _reranker
    with _lock:
        if _reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            _reranker = TextCrossEncoder("Xenova/ms-marco-MiniLM-L-6-v2", threads=2)
    with _infer:
        return [float(x) for x in _reranker.rerank(query, docs, batch_size=_EMBED_BATCH)]


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


def _section_name(block: str) -> str | None:
    """'Results', '3. Results', 'III. Discussion', '2.1 Methods' -> comparable name; any other
    short line (subsection title, author list, table row) is not a heading for labelling."""
    m = _SECTION.fullmatch(block)
    return m.group(1).lower().rstrip("s") if m else None


def paragraphs(text: str) -> list[tuple[str | None, str]]:
    """(section heading or None, paragraph) per blank-line separated block; recognised headings
    are not paragraphs. Never guesses: text before the first recognised heading (title, authors,
    abstract) is None; a heading name seen twice means a structured abstract, so everything
    before that repeat is None too; after a 'Figure 2' / 'Table II' label the section is None
    until the next recognised heading, so captions and text after an inline float get no label."""
    blocks = [b for b in (b.strip() for b in _BLOCK.split(text)) if b]
    names = [_section_name(b) for b in blocks]
    seen, body = set(), 0
    for i, name in enumerate(names):
        if name and name in seen:
            body = i
            break
        seen.add(name)
    out: list[tuple[str | None, str]] = []
    section = None
    for i, (block, name) in enumerate(zip(blocks, names, strict=True)):
        if name:
            section = block if i >= body else None
            continue
        if _FLOAT_LABEL.match(block):
            section = None
        out.append((section, block))
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
