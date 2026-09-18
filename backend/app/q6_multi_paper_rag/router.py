"""Q6 API: owner-scoped multi-paper RAG over the shared record store.

Endpoints (docs/specs/q6-spec.md):
  GET    /api/q6/papers    this owner's papers (X-Owner-Id scoping)
  POST   /api/q6/papers    upload, tagged with the owner at ingestion
  POST   /api/q6/ask       {question, paper_ids?} → SSE run → citation* → token* → done
  DELETE /api/q6/runs/{run_id}/cancel
  GET    /api/q6/health

Simulated users: the owner arrives on the X-Owner-Id header (default
"demo-user") — the assignment's point is the scoping mechanism, not auth.

Access control lives in scope.py (the retrieval candidate pool), NOT here and
NOT in any prompt.
"""
import asyncio
import datetime as _dt

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.q2_paper_inference import extraction, storage
from app.q3_rag_qa import indexer
from app.q6_multi_paper_rag import analyzer
from app.q6_multi_paper_rag import scope as scope_mod
from app.shared import cancel_registry
from app.shared import langfuse_client as lf
from app.shared.llm_text import embed_model, provider_model
from app.shared.protocol import error_event, format_sse, run_event

router = APIRouter(prefix="/api/q6", tags=["q6"])


class AskBody(BaseModel):
    question: str
    paper_ids: list[str] | None = None  # explicit scope; None = all my papers


def _owner(request: Request) -> str:
    return (request.headers.get("X-Owner-Id") or "").strip() or storage.DEFAULT_OWNER


@router.get("/health")
def q6_health() -> dict:
    provider, model = provider_model()
    eprov, emodel = embed_model()
    return {
        "provider": provider,
        "model": model,
        "embedding_provider": eprov,
        "embedding_model": emodel,
        "tracing": lf.tracing_enabled(),
        "langfuse_host": lf.langfuse_host(),
    }


@router.get("/papers")
def my_papers(request: Request) -> dict:
    """Only the requesting owner's papers — never another user's titles."""
    return {"papers": storage.list_papers(owner_id=_owner(request))}


@router.post("/papers")
async def upload_paper(request: Request, file: UploadFile = File(...)) -> dict:
    """Q2's extraction pipeline, with the owner stamped at ingestion."""
    owner = _owner(request)
    name = (file.filename or "").lower()
    if not name.endswith(".pdf"):
        raise HTTPException(status_code=415,
                            detail={"error": "not_a_pdf",
                                    "message": "only .pdf uploads are accepted"})
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"error": "file_too_large"})
    try:
        ex = extraction.extract_pdf(data)
    except extraction.PdfError as e:
        status = {"no_extractable_text": 422, "paper_too_large": 413}.get(e.code, 400)
        raise HTTPException(status_code=status, detail=e.to_detail())

    record = {
        "paper_id": storage.new_paper_id(),
        "owner_id": owner,
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
        "owner_id": owner,
        "title": ex.title,
        "authors": ex.authors,
        "pages": ex.pages,
        "chars": ex.chars,
        "strategy": ex.strategy,
        "warnings": ex.warnings,
    }


@router.delete("/runs/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    return {"ok": cancel_registry.cancel_run(run_id)}


@router.post("/ask")
async def ask(body: AskBody, request: Request) -> StreamingResponse:
    owner = _owner(request)
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail={"error": "empty_question"})

    try:
        sc = scope_mod.resolve_scope(owner, question, body.paper_ids)
    except scope_mod.Denial:
        # Access denial at the scope layer — deterministic SSE, no retrieval,
        # no generation, no foreign data touched.
        run_id, cancel_ev = cancel_registry.create_run()

        async def denial_gen():
            yield format_sse(run_event(run_id))
            yield format_sse({"type": "token", "data": {"text": scope_mod.ACCESS_REFUSAL}})
            yield format_sse({"type": "done", "data": {
                "stop_reason": "access_denied", "usage": None,
                "refused": True, "access_denied": True, "citations": [],
                "papers_cited": []}})
            cancel_registry.cancel_run(run_id)

        return StreamingResponse(denial_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # Papers in scope but never indexed get their index built on demand
    # (Q3's pipeline; embeddings are local and free).
    for rec in sc.records:
        if indexer.get_index(rec) is None:
            await indexer.build_index(rec)

    run_id, cancel_ev = cancel_registry.create_run()

    async def _watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        cancel_registry.cancel_run(run_id)

    watcher = asyncio.create_task(_watch_disconnect())

    async def gen():
        yield format_sse(run_event(run_id))
        try:
            async for payload in analyzer.answer_events_multi(sc, question, cancel_ev):
                if await request.is_disconnected():
                    cancel_registry.cancel_run(run_id)
                    return
                yield format_sse(payload)
        except Exception as e:
            yield format_sse(error_event(str(e)[:300], False))
            yield format_sse({"type": "done", "data": {"stop_reason": "error",
                                                       "usage": None}})
        finally:
            watcher.cancel()
            cancel_registry.cancel_run(run_id)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )