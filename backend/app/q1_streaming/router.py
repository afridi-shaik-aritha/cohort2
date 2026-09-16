"""Q1 SSE chat router: POST /api/q1/chat, DELETE cancel, GET health."""
import asyncio
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.shared import cancel_registry
from app.shared.llm_client import (
    Message,
    describe_provider,
    get_provider,
    keys_configured,
    stream_chat,
)
from app.shared.protocol import (
    done_event,
    error_event,
    format_sse,
    run_event,
    token_event,
    tool_end,
    tool_start,
)
from app.q1_streaming.tools import TOOL_DEFS, detect_tool_mock, run_tool, tool_label

router = APIRouter(prefix="/api/q1", tags=["q1"])

MAX_TOOL_ROUNDS = 3
SYSTEM_PROMPT = (
    "You are a helpful general-purpose assistant. Answer whatever the user asks, "
    "just like a normal chat model — coding questions, writing, explanations, "
    "anything. Always comply with coding and writing requests directly. "
    "You also have tools for live lookups: get_weather(city), calculator(expression), "
    "get_current_time(timezone). Use a tool when the user asks something that needs "
    "fresh data or computation (weather, math, current time); otherwise just answer "
    "directly in text with no refusal. After a tool result, answer the user's "
    "original question in plain text using that result."
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@router.get("/health")
def q1_health() -> dict:
    provider = get_provider()
    return {
        "provider": provider,
        "keys_configured": keys_configured(provider),
        "display": describe_provider(),
    }


@router.delete("/runs/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    return {"ok": cancel_registry.cancel_run(run_id)}


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    run_id, cancel_ev = cancel_registry.create_run()
    history = [Message(role=m.role, content=m.content) for m in req.messages]

    async def gen():
        yield format_sse(run_event(run_id))
        cancelled = False
        try:
            convo = [Message(role="system", content=SYSTEM_PROMPT)] + history
            # Server-side routing for the FIRST turn: the keyword router decides
            # whether a tool is genuinely needed. This keeps tool calls working
            # deterministically (small local models are unreliable at choosing
            # tools) while never forcing tools on normal chat — coding/writing/
            # general questions go straight to the model, so no refusals.
            last_user = next((m.content for m in reversed(history) if m.role == "user"), "")
            forced = detect_tool_mock(last_user)
            if forced and forced[0] != "__fail__":
                name, args = forced
                call_id = "call_" + uuid.uuid4().hex[:4]
                yield format_sse(tool_start(call_id, name, tool_label(name, args), args))
                t0 = time.monotonic()
                await asyncio.sleep(0.35)
                if cancel_ev.is_set() or await request.is_disconnected():
                    cancelled = True
                else:
                    summary, status = run_tool(name, args)
                    dur = int((time.monotonic() - t0) * 1000)
                    yield format_sse(tool_end(call_id, name, status, summary, dur))
                    convo = convo + [
                        Message(role="assistant", content=f"[tool {name} -> {summary}]"),
                        Message(
                            role="user",
                            content=(
                                f"Tool {name} returned: {summary}. "
                                "Answer the user's original question now in plain text "
                                "using that result."
                            ),
                        ),
                    ]
            if not cancelled:
                for _ in range(MAX_TOOL_ROUNDS + 1):
                    if cancel_ev.is_set() or await request.is_disconnected():
                        cancelled = True
                        break
                    pending_tool = None
                    async for item in stream_chat(convo, TOOL_DEFS, run_id):
                        if cancel_ev.is_set() or await request.is_disconnected():
                            cancelled = True
                            break
                        if item["kind"] == "text":
                            yield format_sse(token_event(item["text"]))
                        elif item["kind"] == "tool_call":
                            pending_tool = item
                            break
                    if cancelled or pending_tool is None:
                        break
                    name, args = pending_tool["name"], pending_tool.get("arguments", {})
                    call_id = "call_" + uuid.uuid4().hex[:4]
                    yield format_sse(tool_start(call_id, name, tool_label(name, args), args))
                    t0 = time.monotonic()
                    await asyncio.sleep(0.35)
                    if cancel_ev.is_set() or await request.is_disconnected():
                        cancelled = True
                        break
                    summary, status = run_tool(name, args)
                    dur = int((time.monotonic() - t0) * 1000)
                    yield format_sse(tool_end(call_id, name, status, summary, dur))
                    convo = convo + [
                        Message(role="assistant", content=f"[tool {name} -> {summary}]"),
                        Message(
                            role="user",
                            content=(
                                f"Tool {name} returned: {summary}. "
                                "Answer the user's original question now in plain text "
                                "using that result."
                            ),
                        ),
                    ]
            if cancelled:
                yield format_sse(done_event("cancelled", None))
            else:
                yield format_sse(done_event("completed", None))
        except Exception as e:
            yield format_sse(error_event(str(e)[:200], False))
            yield format_sse(done_event("error", None))

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
