"""FastAPI shell: job API, exports, chat, static UI. Jobs live in memory (single process)."""

import asyncio
import logging
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | done | error
    stage: str = ""
    done: int = 0
    total: int = 0
    error: str | None = None
    report: Report | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def view(self) -> dict:
        return {
            "status": self.status,
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "error": self.error,
            "report": self.report.model_dump(mode="json") if self.report else None,
        }


# ponytail: in-memory, one process; jobs vanish on restart. Use a DB if we ever scale out.
JOBS: OrderedDict[str, Job] = OrderedDict()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.running = asyncio.Semaphore(MAX_RUNNING)
    for p in get_settings().problems():
        log.warning("config: %s", p)
    yield


app = FastAPI(title="ClaimTrace", lifespan=lifespan)


async def _run(job: Job, text: str | None, files: list[InputFile]) -> None:
    def progress(stage: str, done: int, total: int) -> None:
        job.stage, job.done, job.total = stage, done, total

    async with app.state.running:
        job.status = "running"
        try:
            job.report = await pipeline.run(text, files, progress, report_id=job.id)
            job.status = "done"
        except Exception as e:
            log.exception("job %s failed", job.id)
            # redact before cutting so a key split at the cut cannot survive
            job.error, job.status = redact(f"{type(e).__name__}: {e}")[:300], "error"


def _evict() -> None:
    """Keep at most MAX_JOBS: drop the oldest finished ones; if all are unfinished, refuse."""
    while len(JOBS) >= MAX_JOBS:
        old = next((k for k, j in JOBS.items() if j.status in ("done", "error")), None)
        if old is None:
            raise HTTPException(503, "Server busy: too many jobs in progress, try again later.")
        del JOBS[old]


@app.post("/api/jobs")
async def create_job(
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
    _evict()
    job = Job(id=uuid.uuid4().hex[:12])
    JOBS[job.id] = job
    job.task = asyncio.create_task(_run(job, text, inputs))
    return {"id": job.id}


def _job(job_id: str) -> Job:
    if job_id not in JOBS:
        raise HTTPException(404, "Unknown job (jobs are kept in memory and may have expired).")
    return JOBS[job_id]


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
async def chat_route(job_id: str, body: ChatIn) -> dict:
    report = _report(job_id)
    try:
        answer = await chat.answer(report, body.question, body.history)
    except Exception as e:
        log.warning("chat failed for job %s: %s", job_id, type(e).__name__)
        raise HTTPException(502, "The assistant is unavailable right now.") from e
    return {"answer": answer}


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
