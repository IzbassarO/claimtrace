"""FastAPI shell: job API, exports, chat, static UI. Jobs live in memory (single process);
finished reports are also written to CACHE_DIR/reports so shareable links survive restarts."""

import asyncio
import logging
import re
import secrets
import time
import traceback
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from claimtrace import pipeline
from claimtrace.core.http import redact
from claimtrace.core.schemas import InputFile, Report
from claimtrace.core.settings import get_settings
from claimtrace.web import chat, export

log = logging.getLogger("claimtrace.web")
STATIC = Path(__file__).parent / "static"
ALLOWED_EXT = {".txt", ".md", ".bib", ".pdf"}
MAX_JOBS = 50
MAX_RUNNING = 2
MAX_REPORTS = 1000
MAX_QUEUED = 4  # uploads waiting for a worker are held in RAM
JOB_TIMEOUT_S = 1200
RATE_JOBS, RATE_WINDOW_S = 10, 600  # per client IP
MAX_CHAT_RUNNING = 2
MAX_CHAT_PER_REPORT = 20
# job id = report id = permalink token (the link is the only access control): token_urlsafe(16)
JOB_ID = re.compile(r"[A-Za-z0-9_-]{22}")


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | done | error
    stage: str = ""
    done: int = 0
    total: int = 0
    error: str | None = None
    report: Report | None = None
    partial: Report | None = None  # latest snapshot while running; memory only, never saved
    task: asyncio.Task | None = field(default=None, repr=False)

    def view(self) -> dict:
        return {
            "status": self.status,
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "error": self.error,
            "report": (r := self.report or self.partial) and r.model_dump(mode="json"),
        }


# ponytail: in-memory, one process; only finished reports survive a restart (on disk).
JOBS: OrderedDict[str, Job] = OrderedDict()


def _reports_dir() -> Path:
    return Path(get_settings().cache_dir) / "reports"


def _save(job_id: str, report: Report) -> None:
    """Atomic write (tmp + replace), then keep only the newest MAX_REPORTS files."""
    d = _reports_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{job_id}.json.tmp"
    tmp.write_text(report.model_dump_json(), encoding="utf-8")
    tmp.replace(d / f"{job_id}.json")
    # ponytail: mtime scan of the whole dir on every write; fine for 1000 files, use a DB beyond
    files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime_ns, reverse=True)
    for old in files[MAX_REPORTS:]:
        old.unlink(missing_ok=True)


def _load(job_id: str) -> Report | None:
    """Saved report, or None. Callers must have checked JOB_ID (no path traversal)."""
    path = _reports_dir() / f"{job_id}.json"
    try:
        return Report.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("cannot read saved report %s: %s", job_id, type(e).__name__)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.running = asyncio.Semaphore(MAX_RUNNING)
    app.state.chat_running = asyncio.Semaphore(MAX_CHAT_RUNNING)
    # ponytail: per-process dicts that only grow (one int / few floats per report / client IP);
    # fine for a demo server, move to a TTL store if it runs for months
    app.state.chat_counts = {}
    app.state.job_times = {}
    for p in get_settings().problems():
        log.warning("config: %s", p)
    yield


class UploadLimit:
    """Pure ASGI: reject an oversized POST /api/jobs before Starlette spools the multipart.
    Content-Length is checked up front; bodies without one are counted while streaming."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope["method"], scope["path"]) != ("POST", "/api/jobs"):
            return await self.app(scope, receive, send)
        limit = (get_settings().max_upload_mb + 1) * 1024 * 1024  # +1 MB: multipart overhead
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            return await _too_large(send)
        seen, over = 0, False

        async def counted():
            nonlocal seen, over
            msg = await receive()
            seen += len(msg.get("body", b""))
            if seen > limit:
                over = True
                raise ValueError("upload too large")
            return msg

        async def guarded(msg):
            if not over:  # whatever the app answers to the aborted body is replaced by 413
                await send(msg)

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            if not over:
                raise
        if over:
            await _too_large(send)


async def _too_large(send) -> None:
    mb = get_settings().max_upload_mb
    body = f'{{"detail":"Upload too large (max {mb} MB in total)."}}'.encode()
    headers = [(b"content-type", b"application/json"), (b"connection", b"close")]
    await send({"type": "http.response.start", "status": 413, "headers": headers})
    await send({"type": "http.response.body", "body": body})


app = FastAPI(title="ClaimTrace", lifespan=lifespan)
app.add_middleware(UploadLimit)


async def _run(job: Job, text: str | None, files: list[InputFile]) -> None:
    def progress(stage: str, done: int, total: int) -> None:
        job.stage, job.done, job.total = stage, done, total

    def partial(report: Report) -> None:
        job.partial = report

    async with app.state.running:
        job.status = "running"
        try:
            async with asyncio.timeout(JOB_TIMEOUT_S) as deadline:
                job.report = await pipeline.run(
                    text, files, progress, report_id=job.id, on_partial=partial
                )
        except Exception as e:
            job.report = job.partial = None
            # tracebacks carry request URLs (API keys): never log.exception here
            log.error("job %s failed:\n%s", job.id, redact(traceback.format_exc()))
            if deadline.expired():
                job.error = f"The audit timed out after {JOB_TIMEOUT_S / 60:g} min."
            else:  # redact before cutting so a key split at the cut cannot survive
                job.error = redact(f"{type(e).__name__}: {e}")[:300]
            job.status = "error"
            return
        try:
            _save(job.id, job.report)
        except Exception as e:  # the report stays available in memory
            log.warning("could not save report %s: %s", job.id, type(e).__name__)
        job.partial = None
        job.status = "done"


def _evict() -> None:
    """Keep at most MAX_JOBS: drop the oldest finished ones; if all are unfinished, refuse."""
    while len(JOBS) >= MAX_JOBS:
        old = next((k for k, j in JOBS.items() if j.status in ("done", "error")), None)
        if old is None:
            raise HTTPException(503, "Server busy: too many jobs in progress, try again later.")
        del JOBS[old]
    if sum(j.status == "queued" for j in JOBS.values()) >= MAX_QUEUED:
        raise HTTPException(
            503, "Server busy: too many audits waiting, try again in a few minutes."
        )


def _client_ip(request: Request) -> str:
    """Caddy sets X-Forwarded-For to the real client (it drops untrusted incoming values)."""
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "?")


@app.post("/api/jobs")
async def create_job(
    request: Request,
    text: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> dict:
    text = text if text and text.strip() else None
    max_mb = get_settings().max_upload_mb
    too_big = HTTPException(413, f"Upload too large (max {max_mb} MB in total).")
    budget = max_mb * 1024 * 1024 - len((text or "").encode())
    if budget < 0:
        raise too_big
    inputs = []
    for f in files or []:
        name = PurePath(f.filename or "").name
        if not name:  # browsers send an empty part when no file is chosen
            continue
        if PurePath(name).suffix.lower() not in ALLOWED_EXT:
            raise HTTPException(415, f"Unsupported file type: {name} (allowed: .txt .md .bib .pdf)")
        content = await f.read(budget + 1)
        budget -= len(content)
        if budget < 0:
            raise too_big
        inputs.append(InputFile(name=name, content=content))
    if text is None and not inputs:
        raise HTTPException(400, "Paste manuscript text or upload at least one file.")
    ip, now = _client_ip(request), time.monotonic()
    recent = [t for t in request.app.state.job_times.get(ip, []) if now - t < RATE_WINDOW_S]
    if len(recent) >= RATE_JOBS:
        raise HTTPException(429, "Too many audits from your address, try again in a few minutes.")
    _evict()
    request.app.state.job_times[ip] = [*recent, now]
    job = Job(id=secrets.token_urlsafe(16))
    JOBS[job.id] = job
    job.task = asyncio.create_task(_run(job, text, inputs))
    return {"id": job.id}


def _job(job_id: str) -> Job:
    if job_id in JOBS:
        return JOBS[job_id]
    if JOB_ID.fullmatch(job_id) and (report := _load(job_id)):
        return Job(id=job_id, status="done", report=report)
    raise HTTPException(404, "Unknown report (it may have expired).")


def _report(job_id: str) -> Report:
    job = _job(job_id)
    if job.report is None:
        raise HTTPException(409, f"Job is {job.status}, no report yet.")
    return job.report


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    return _job(job_id).view()


@app.get("/api/jobs/{job_id}/export.json")
async def export_json(job_id: str) -> Response:
    return Response(
        _report(job_id).model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="claimtrace-{job_id}.json"'},
    )


@app.get("/api/jobs/{job_id}/export.md")
async def export_md(job_id: str) -> PlainTextResponse:
    return PlainTextResponse(
        export.to_markdown(_report(job_id)),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="claimtrace-{job_id}.md"'},
    )


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[chat.Turn] = Field(default_factory=list, max_length=20)


@app.post("/api/jobs/{job_id}/chat")
async def chat_route(job_id: str, body: ChatIn, request: Request) -> dict:
    report = _report(job_id)
    state = request.app.state
    if state.chat_counts.get(job_id, 0) >= MAX_CHAT_PER_REPORT:
        raise HTTPException(429, f"Question limit reached ({MAX_CHAT_PER_REPORT} per report).")
    if state.chat_running.locked():
        raise HTTPException(429, "The assistant is busy, retry in a moment.")
    try:
        async with state.chat_running:
            state.chat_counts[job_id] = state.chat_counts.get(job_id, 0) + 1
            answer = await chat.answer(report, body.question, body.history)
    except Exception as e:
        log.warning("chat failed for job %s: %s", job_id, type(e).__name__)
        raise HTTPException(502, "The assistant is unavailable right now.") from e
    return {"answer": answer}


@app.get("/api/info")
async def info() -> dict:
    """Facts the page states instead of hard-coding them."""
    return {"max_reports": MAX_REPORTS}


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/r/{job_id}", include_in_schema=False)
async def permalink(job_id: str) -> FileResponse:
    """The page loads the report itself from /api/jobs/{id}."""
    if not JOB_ID.fullmatch(job_id):
        raise HTTPException(404, "Unknown report.")
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
