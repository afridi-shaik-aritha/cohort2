"""Q2 API: upload a paper, analyze it with per-section SSE progress, cancel a run."""
import asyncio
import datetime as _dt
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.q2_paper_inference import analyzer, extraction, storage
from app.shared import cancel_registry
from app.shared import langfuse_client as lf
from app.shared.llm_text import provider_model
from app.shared.protocol import error_event, format_sse, run_event

router = APIRouter(prefix="/api/q2", tags=["q2"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.get("/health")
def q2_health() -> dict:
    provider, model = provider_model()
    return {
        "provider": provider,
        "model": model,
        "tracing": lf.tracing_enabled(),
        "langfuse_host": lf.langfuse_host(),
    }


@router.get("/papers")
def list_papers(owner_id: Optional[str] = None) -> dict:
    return {"papers": storage.list_papers(owner_id=owner_id)}


@router.get("/papers/{paper_id}")
def get_paper(paper_id: str) -> dict:
    rec = storage.load_paper(paper_id)
    if rec is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return rec


@router.delete("/runs/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    return {"ok": cancel_registry.cancel_run(run_id)}


@router.post("/papers")
async def upload_paper(file: UploadFile = File(...)) -> dict:
    name = (file.filename or "").lower()
    if not name.endswith(".pdf"):
        raise HTTPException(
            status_code=415,
            detail={"error": "not_a_pdf", "message": "only .pdf uploads are accepted"},
        )
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "file_too_large",
                "message": f"{len(data) // 1024 // 1024}MB exceeds the 25MB upload limit",
            },
        )
    try:
        ex = extraction.extract_pdf(data)
    except extraction.PdfError as e:
        status = {"no_extractable_text": 422, "paper_too_large": 413}.get(e.code, 400)
        raise HTTPException(status_code=status, detail=e.to_detail())

    record = {
        "paper_id": storage.new_paper_id(),
        "owner_id": storage.DEFAULT_OWNER,
        "title": ex.title,
        "authors": ex.authors,
        "pages": ex.pages,
        "chars": ex.chars,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "filename": file.filename,
        "extraction": {"strategy": ex.strategy, "warnings": ex.warnings},
        "text": ex.text,
        "sections": {},
        "usage": {},
    }
    storage.save_paper(record)
    return {
        "paper_id": record["paper_id"],
        "title": ex.title,
        "authors": ex.authors,
        "pages": ex.pages,
        "chars": ex.chars,
        "strategy": ex.strategy,
        "warnings": ex.warnings,
    }


@router.post("/papers/{paper_id}/analyze")
async def analyze(paper_id: str, request: Request) -> StreamingResponse:
    record = storage.load_paper(paper_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    run_id, cancel_ev = cancel_registry.create_run()

    async def _watch_disconnect():
        """Cancel the run if the client disappears mid-stream. Without this, a
        closed browser tab would leave the analyzer generating for minutes (the
        map-reduce digest phase emits no SSE frames, so gen() wouldn't notice
        until the next yield)."""
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        cancel_registry.cancel_run(run_id)

    watcher = asyncio.create_task(_watch_disconnect())

    async def gen():
        yield format_sse(run_event(run_id))
        try:
            async for payload in analyzer.analyze_events(record, cancel_ev=cancel_ev):
                if await request.is_disconnected():
                    cancel_registry.cancel_run(run_id)
                    return
                yield format_sse(payload)
        except Exception as e:  # never leave the stream hanging
            yield format_sse(error_event(str(e)[:300], False))
            yield format_sse(
                {"type": "done", "data": {"stop_reason": "error", "usage": None}}
            )
        finally:
            watcher.cancel()
            cancel_registry.cancel_run(run_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
