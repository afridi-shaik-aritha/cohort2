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


def done_event(stop_reason: str, usage: Optional[dict], **extras) -> dict:
    """Q1/Q2 shape preserved; Q3 adds refused/citations via **extras."""
    data = {"stop_reason": stop_reason, "usage": usage}
    data.update(extras)
    return {"type": "done", "data": data}


def citation_event(position: int, label: str, snippet: str) -> dict:
    """Q3: a source the answer is grounded in, emitted before tokens so the UI
    can render citation chips while the answer streams."""
    return {
        "type": "citation",
        "data": {"position": position, "label": label, "snippet": snippet},
    }


def section_start(key: str, label: str, index: int, total: int) -> dict:
    """Q2: a per-section generation began."""
    return {
        "type": "section_start",
        "data": {"key": key, "label": label, "index": index, "total": total},
    }


def section_token(key: str, text: str) -> dict:
    """Q2: a token chunk tagged with the section it belongs to."""
    return {"type": "token", "data": {"section": key, "text": text}}


def section_done(key: str, chars: int, usage: Optional[dict], duration_ms: int) -> dict:
    """Q2: a per-section generation finished."""
    return {
        "type": "section_done",
        "data": {"key": key, "chars": chars, "usage": usage, "duration_ms": duration_ms},
    }


def format_sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
