"""Q1 SSE event constructors. Shapes must match docs/specs/q1-spec.md section 1."""
import json
from typing import Optional


def run_event(run_id: str) -> dict:
    return {"type": "run", "data": {"run_id": run_id}}


def token_event(text: str) -> dict:
    return {"type": "token", "data": {"text": text}}


def tool_start(call_id: str, tool: str, label: str, args: dict) -> dict:
    return {
        "type": "tool_call_start",
        "data": {"id": call_id, "tool": tool, "label": label, "args": args},
    }


def tool_end(call_id: str, tool: str, status: str, summary: str, duration_ms: int) -> dict:
    return {
        "type": "tool_call_end",
        "data": {
            "id": call_id,
            "tool": tool,
            "status": status,
            "summary": summary,
            "duration_ms": duration_ms,
        },
    }


def error_event(message: str, recoverable: bool) -> dict:
    return {"type": "error", "data": {"message": message, "recoverable": recoverable}}


def done_event(stop_reason: str, usage: Optional[dict]) -> dict:
    return {"type": "done", "data": {"stop_reason": stop_reason, "usage": usage}}


def format_sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
