"""Pick the source passages most relevant to a citing sentence: BM25 over all passages, then a
dense rerank of the BM25 top candidates, fused by RRF; a cross-encoder reorders the fused top.
section_of() labels a verified quote with its source section (not used for retrieval).
Passages, the BM25 index and passage embeddings are cached per source text: many claims cite the
same source."""

import asyncio
import hashlib
import re
import threading
from array import array
from collections import OrderedDict
from collections.abc import Callable, Sequence

from rank_bm25 import BM25Okapi

from claimtrace.claims.quote import verify_quote
from claimtrace.core.settings import get_settings
from claimtrace.core.timing import span

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
# 'Figure 2.', 'Fig. 2', 'Table II', 'Table 1: ...', 'Fig. S1', 'Extended Data Fig. 1',
# 'Supplementary Table 2', 'S1 Fig'; not 'Table 1 shows ...' (body text)
_FLOAT_LABEL = re.compile(
    r"(?i)(?:(?:extended data|supplementary)\s+)?"
    r"(?:(?:fig(?:ure)?\.?|table|box|scheme)\s*(?:[a-z]?\d+[a-z]?|[ivxlc]+)"
    r"|s\d+\s+(?:fig(?:ure)?|table))\b\s*(?:$|[.:])"
)
_NUMBERED = re.compile(r"(?i)(?:\d+(?:\.\d+)*|[ivx]+)\.?\s")  # '2 ', '3. ', 'III. ', '2.1 '
_SUBSECTION = re.compile(r"\d+\.\d")  # '2.1 Participants': stays in its section
# a one-line title: no table cells, no sentence end ('Case presentation', '2 Patients and methods')
_HEADING_LIKE = re.compile(r"[A-Z0-9][^\n|]*[^\s.,:;!?]")
_HEADING_MAX_WORDS = 8

_lock = threading.Lock()  # lazy model loading only
# ponytail: 2 concurrent inferences (ONNX `run` is thread-safe); each ONNX session uses 2 threads
# and batch 16, so memory stays bounded. Raise with care: the container is capped at 1.5 GB.
_infer = threading.BoundedSemaphore(max(1, get_settings().retrieval_slots))
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

            threads = get_settings().retrieval_threads
            _model = TextEmbedding("BAAI/bge-small-en-v1.5", threads=threads)
    with _infer:
        return [v.tolist() for v in _model.embed(texts, batch_size=_EMBED_BATCH)]


def fastembed_rerank(query: str, docs: list[str]) -> list[float]:
    """Default cross-encoder (blocking; call in a thread). Loads once, lazily; inference shares
    the embedder's 2-slot semaphore, so at most 2 ONNX runs (of either model) at a time."""
    global _reranker
    with _lock:
        if _reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            threads = get_settings().retrieval_threads
            _reranker = TextCrossEncoder("Xenova/ms-marco-MiniLM-L-6-v2", threads=threads)
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


def _heading_like(block: str) -> bool:
    return bool(_HEADING_LIKE.fullmatch(block)) and len(block.split()) <= _HEADING_MAX_WORDS


def paragraphs(text: str) -> list[tuple[str | None, str]]:
    """(section heading or None, paragraph) per blank-line separated block; recognised headings
    are not paragraphs. Never guesses: text before the first recognised heading (title, authors,
    abstract) is None; when the paper numbers its headings ('1 Introduction'), unnumbered ones
    (a structured abstract's 'Results') do not count; a heading name seen twice means a
    structured abstract, so everything before that repeat is None too; any other heading-like
    line ('Case presentation', '2 Patients and methods', except numbered subsections like
    '2.1 Participants') and a 'Figure 2' / 'Table II' label end the section: None until the next
    recognised heading, so text under an unknown heading and captions get no label."""
    blocks = [b for b in (b.strip() for b in _BLOCK.split(text)) if b]
    names = [_section_name(b) for b in blocks]
    numbered = [bool(n and _NUMBERED.match(b)) for b, n in zip(blocks, names, strict=True)]
    if any(numbered):
        names = [n if num else None for n, num in zip(names, numbered, strict=True)]
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
        if _FLOAT_LABEL.match(block) or (_heading_like(block) and not _SUBSECTION.match(block)):
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


class _Index:
    """One source text: its passages, their BM25 index (None: no word characters, nothing to
    rank) and, per embedder, the vectors of the passages embedded so far."""

    def __init__(self, text: str):
        self.chunks = passages(text)
        corpus = [_tokens(c) for c in self.chunks]
        self.bm25 = BM25Okapi(corpus) if any(corpus) else None
        self.vecs: dict[Embed, dict[int, array]] = {}


# ponytail: in-process LRU of the last 32 sources (a few MB each at most); keyed by sha256 so a
# different text never reuses an index. Shared across runs; a disk cache only if restarts matter.
_INDEX_SIZE = 32
_indexes: OrderedDict[str, _Index] = OrderedDict()
_index_lock = threading.Lock()


def _index(text: str) -> _Index:
    key = hashlib.sha256(text.encode()).hexdigest()
    with _index_lock:
        if (idx := _indexes.get(key)) is not None:
            _indexes.move_to_end(key)
            return idx
    idx = _Index(text)  # built outside the lock; a duplicate build by a racing thread is harmless
    with _index_lock:
        _indexes[key] = idx
        while len(_indexes) > _INDEX_SIZE:
            _indexes.popitem(last=False)
    return idx


def _embed_candidates(idx: _Index, query: str, cands: list[int], embed: Embed) -> list:
    """[query vector, *candidate vectors]; only candidates not embedded before are sent to the
    model (with the query, in one call). Blocking: call in a thread."""
    memo = idx.vecs.setdefault(embed, {})
    missing = [i for i in cands if i not in memo]
    vecs = embed([query, *(idx.chunks[i] for i in missing)])
    if len(vecs) != 1 + len(missing):
        raise ValueError("embedder returned the wrong number of vectors")
    for i, v in zip(missing, vecs[1:], strict=True):
        memo[i] = array("d", v)
    return [vecs[0], *(memo[i] for i in cands)]


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
    with span("retrieval"):
        return await _retrieve(query, text, embed or fastembed_embed, rerank or fastembed_rerank)


async def _retrieve(query: str, text: str, embed: Embed, rerank: Rerank) -> list[str]:
    idx = _index(text)
    chunks = idx.chunks
    if len(chunks) <= TOP_K:
        return chunks
    if idx.bm25 is None:  # no word characters at all: BM25 divides by zero, nothing to rank
        return chunks[:TOP_K]
    bm25 = _ranks(idx.bm25.get_scores(_tokens(query)))[:CANDIDATES]
    rankings = [bm25]
    try:
        vecs = await asyncio.to_thread(_embed_candidates, idx, query, bm25, embed)
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
        scores = await asyncio.to_thread(rerank, query, [chunks[i] for i in pool])
        if len(scores) != len(pool):
            raise ValueError("reranker returned the wrong number of scores")
        order = [pool[r] for r in _ranks(scores)]
    except Exception:  # the reranker is a bonus: keep the hybrid order
        pass
    return [chunks[i] for i in sorted(order[:TOP_K])]
