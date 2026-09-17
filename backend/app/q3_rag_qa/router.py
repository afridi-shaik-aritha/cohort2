"""Q3 API: index a paper, ask questions over it (SSE), cancel a run.

Endpoints (docs/specs/q3-spec.md "API contract"):
  POST   /api/q3/papers/{id}/index   build/refresh the retrieval index
  GET    /api/q3/papers/{id}/index   index status for the UI chip
  POST   /api/q3/papers/{id}/ask     {question} → SSE run → citation* → token* → done
  DELETE /api/q3/runs/{run_id}/cancel
  GET    /api/q3/health

`ask` builds the index on demand when missing (spec: "called automatically
after Q2's analyze completes, and on demand before the first question").
"""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.q2_paper_inference import storage
from app.q3_rag_qa import analyzer, indexer
from app.shared import cancel_registry
from app.shared import langfuse_client as lf
from app.shared.llm_text import embed_model, provider_model
from app.shared.protocol import error_event, format_sse, run_event

router = APIRouter(prefix="/api/q3", tags=["q3"])


class AskBody(BaseModel):
    question: str


@router.get("/health")
def q3_health() -> dict:
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


@router.post("/papers/{paper_id}/index")
async def build_index(paper_id: str) -> dict:
    record = storage.load_paper(paper_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    try:
        return await indexer.build_index(record)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail={"error": "embedding_failed",
                                                     "message": str(e)[:300]})


@router.get("/papers/{paper_id}/index")
def index_status(paper_id: str) -> dict:
    record = storage.load_paper(paper_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    idx = indexer.get_index(record)
    if idx is None:
        return {"indexed": False}
    return {
        "indexed": True,
        "chunks": len(idx.get("chunks") or []),
        "has_references": bool(idx.get("references")),
        "references_chars": len(idx.get("references") or ""),
        "embedding_provider": idx.get("embedding_provider"),
        "embedding_model": idx.get("embedding_model"),
        "built_at": idx.get("built_at"),
        "qa_turns": len(record.get("qa_history") or []),
    }


@router.delete("/runs/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    return {"ok": cancel_registry.cancel_run(run_id)}


@router.post("/papers/{paper_id}/ask")
async def ask(paper_id: str, body: AskBody, request: Request) -> StreamingResponse:
    record = storage.load_paper(paper_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail={"error": "empty_question"})
    if indexer.get_index(record) is None:
        await indexer.build_index(record)  # on-demand first question (spec)

    run_id, cancel_ev = cancel_registry.create_run()

    async def _watch_disconnect():
        """Cancel the run if the client disappears mid-stream (Q2 pattern)."""
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        cancel_registry.cancel_run(run_id)

    watcher = asyncio.create_task(_watch_disconnect())

    async def gen():
        yield format_sse(run_event(run_id))
        try:
            async for payload in analyzer.answer_events(record, question, cancel_ev):
                if await request.is_disconnected():
                    cancel_registry.cancel_run(run_id)
                    return
                yield format_sse(payload)
        except Exception as e:  # never leave the stream hanging
            yield format_sse(error_event(str(e)[:300], False))
            yield format_sse({"type": "done", "data": {"stop_reason": "error",
                                                       "usage": None}})
        finally:
            watcher.cancel()
            cancel_registry.cancel_run(run_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
