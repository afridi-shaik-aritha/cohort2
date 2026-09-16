from app.shared.protocol import (
    done_event,
    error_event,
    format_sse,
    run_event,
    token_event,
    tool_end,
    tool_start,
)


def test_event_shapes():
    assert run_event("r_1") == {"type": "run", "data": {"run_id": "r_1"}}
    assert token_event("Hel") == {"type": "token", "data": {"text": "Hel"}}
    s = tool_start("call_1", "get_weather", "Checking weather for Paris…", {"city": "Paris"})
    assert s["type"] == "tool_call_start" and s["data"]["id"] == "call_1"
    e = tool_end("call_1", "get_weather", "ok", "Paris: 18C, cloudy", 412)
    assert e["data"]["status"] == "ok" and e["data"]["duration_ms"] == 412
    assert error_event("boom", True)["data"]["recoverable"] is True
    d = done_event("completed", {"input_tokens": 1, "output_tokens": 2})
    assert d["data"]["stop_reason"] == "completed"
    assert (
        format_sse({"type": "token", "data": {"text": "x"}})
        == 'data: {"type": "token", "data": {"text": "x"}}\n\n'
    )
