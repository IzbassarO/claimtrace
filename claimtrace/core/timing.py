"""Per-run timing: stage wall clock + cumulative time per operation (spans may overlap when
work runs concurrently). One collector per pipeline run, carried by a contextvar so asyncio tasks
and to_thread calls started inside the run share it."""

import contextvars
import time
from collections import Counter, defaultdict
from contextlib import contextmanager

_current: contextvars.ContextVar["Timings | None"] = contextvars.ContextVar("timings", default=None)


class Timings:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.ops: defaultdict[str, float] = defaultdict(float)
        self.counts: Counter[str] = Counter()
        self.marks: dict[str, float] = {}
        self.breakers: dict[str, object] = {}  # core.llm circuit breakers, one per role per run

    def now(self) -> float:
        return time.monotonic() - self.t0

    def mark(self, name: str) -> None:
        """First time an event happened (seconds since start); later calls are ignored."""
        self.marks.setdefault(name, round(self.now(), 2))

    def as_dict(self) -> dict[str, float]:
        out = {f"t_{k}": v for k, v in self.marks.items()}
        out |= {f"busy_{k}": round(v, 2) for k, v in self.ops.items()}
        out |= {f"n_{k}": float(n) for k, n in self.counts.items()}
        out["t_total"] = round(self.now(), 2)
        return out


def start() -> Timings:
    t = Timings()
    _current.set(t)
    return t


def current() -> Timings | None:
    return _current.get()


def mark(name: str) -> None:
    if t := _current.get():
        t.mark(name)


@contextmanager
def span(name: str):
    t, s = _current.get(), time.monotonic()
    try:
        yield
    finally:
        if t:
            t.ops[name] += time.monotonic() - s
            t.counts[name] += 1
