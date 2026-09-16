"""Pluggable streaming LLM client.

Providers:
  mock      — deterministic canned streamer (no keys)
  openai    — OpenAI API (native function-calling)
  anthropic — Anthropic API (native tool-use)
  lmstudio  — local LM Studio (OpenAI-compatible, localhost:1234)
  openrouter, groq, deepinfra, nvidia — OpenAI-compatible hosted APIs;
              each reuses the generic OpenAI-compatible streamer with its
              own base_url + key + model from env (see .env.example).

Unified yield protocol (internal, NOT the SSE wire shape):
  {"kind": "text", "text": str}
  {"kind": "tool_call", "name": str, "arguments": dict}
"""
import asyncio
import os
from dataclasses import dataclass
from typing import AsyncIterator

# OpenAI-compatible hosted providers: provider name -> (env prefix, default base_url)
OPENAI_COMPAT_HOSTED = {
    "openrouter": ("OPENROUTER", "https://openrouter.ai/api/v1"),
    "groq": ("GROQ", "https://api.groq.com/openai/v1"),
    "deepinfra": ("DEEPINFRA", "https://api.deepinfra.com/v1/openai"),
    "nvidia": ("NVIDIA", "https://integrate.api.nvidia.com/v1"),
}


@dataclass
class Message:
    role: str
    content: str


def get_provider() -> str:
    p = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    known = ("mock", "openai", "anthropic", "lmstudio") + tuple(OPENAI_COMPAT_HOSTED)
    return p if p in known else "mock"


def keys_configured(provider: str) -> bool:
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    if provider == "lmstudio":
        return True  # local server, no key needed
    if provider in OPENAI_COMPAT_HOSTED:
        prefix = OPENAI_COMPAT_HOSTED[provider][0]
        return bool(os.getenv(f"{prefix}_API_KEY"))
    return True


def describe_provider() -> str:
    p = get_provider()
    if p == "lmstudio":
        return f"lmstudio:{os.getenv('LMSTUDIO_MODEL', 'liquid/lfm2.5-1.2b')} (live)"
    if p == "openai":
        return "openai (live)" if keys_configured("openai") else "openai (demo mode)"
    if p == "anthropic":
        return "anthropic (live)" if keys_configured("anthropic") else "anthropic (demo mode)"
    if p in OPENAI_COMPAT_HOSTED:
        prefix, _ = OPENAI_COMPAT_HOSTED[p]
        model = os.getenv(f"{prefix}_MODEL", "")
        label = f"{p}:{model}" if model else p
        suffix = "(live)" if keys_configured(p) else "(demo mode)"
        return f"{label} {suffix}"
    return "mock (demo mode)"


async def stream_chat(messages, tools, run_id: str = "") -> AsyncIterator[dict]:
    provider = get_provider()
    if provider == "openai" and keys_configured("openai"):
        async for item in _stream_openai(messages, tools):
            yield item
    elif provider == "anthropic" and keys_configured("anthropic"):
        async for item in _stream_anthropic(messages, tools):
            yield item
    elif provider == "lmstudio":
        async for item in _stream_lmstudio(messages, tools):
            yield item
    elif provider in OPENAI_COMPAT_HOSTED and keys_configured(provider):
        async for item in _stream_openai_compat_hosted(messages, tools, provider):
            yield item
    else:
        async for item in _stream_mock(messages):
            yield item


async def _stream_mock(messages) -> AsyncIterator[dict]:
    """Deterministic canned streamer emitting the same kinds as real LLMs."""
    from app.q1_streaming.tools import detect_tool_mock

    last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
    routed = detect_tool_mock(last_user)
    await asyncio.sleep(0.05)
    if routed and routed[0] == "__fail__":
        for word in "Let me try that tool for you. ".split(" "):
            yield {"kind": "text", "text": word + " "}
            await asyncio.sleep(0.03)
        yield {"kind": "tool_call", "name": "nope_missing_tool", "arguments": {}}
        for word in "That tool failed, but I can still answer. ".split(" "):
            yield {"kind": "text", "text": word + " "}
            await asyncio.sleep(0.03)
        return
    if routed:
        name, args = routed
        for word in "I'll look that up for you. ".split(" "):
            yield {"kind": "text", "text": word + " "}
            await asyncio.sleep(0.04)
        yield {"kind": "tool_call", "name": name, "arguments": args}
        return
    answer = (
        "Hello! I'm running in demo mode (no API key configured). "
        "Ask me about the weather in Paris, ask me to calculate 12 * (3 + 4), "
        "or ask what time it is — those trigger tool calls so you can see "
        "the tool-gap indicator in action."
    )
    for word in answer.split(" "):
        yield {"kind": "text", "text": word + " "}
        await asyncio.sleep(0.025)


async def _stream_openai(messages, tools) -> AsyncIterator[dict]:
    import json as _json

    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": m.role, "content": m.content} for m in messages],
        tools=[{"type": "function", "function": t} for t in tools] if tools else None,
        stream=True,
    )
    pending: dict[int, dict] = {}
    async for chunk in resp:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice or not choice.delta:
            continue
        d = choice.delta
        if d.content:
            yield {"kind": "text", "text": d.content}
        for tc in d.tool_calls or []:
            slot = pending.setdefault(tc.index, {"name": "", "args": ""})
            if tc.function and tc.function.name:
                slot["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                slot["args"] += tc.function.arguments
    for slot in pending.values():
        try:
            args = _json.loads(slot["args"] or "{}")
        except Exception:
            args = {}
        yield {"kind": "tool_call", "name": slot["name"], "arguments": args}


async def _stream_anthropic(messages, tools) -> AsyncIterator[dict]:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
    system, convo = "", []
    for m in messages:
        if m.role == "system":
            system += m.content
        else:
            convo.append({"role": m.role, "content": m.content})
    anth_tools = [
        {"name": t["name"], "description": t.get("description", ""),
         "input_schema": t.get("parameters", {})}
        for t in tools
    ]
    async with client.messages.stream(
        model=model, max_tokens=1024, system=system or None,
        messages=convo, tools=anth_tools or None,
    ) as stream:
        async for chunk in stream:
            if chunk.type == "content_block_delta" and getattr(chunk.delta, "text", None):
                yield {"kind": "text", "text": chunk.delta.text}
        final = await stream.get_final_message()
    for block in final.content:
        if getattr(block, "type", "") == "tool_use":
            args = block.input if isinstance(block.input, dict) else {}
            yield {"kind": "tool_call", "name": block.name, "arguments": args}


async def _stream_openai_compat_hosted(messages, tools, provider: str) -> AsyncIterator[dict]:
    """Hosted OpenAI-compatible providers (OpenRouter, Groq, DeepInfra, NVIDIA NIM).

    Same wire format as OpenAI; only base_url/key/model differ. Unlike the
    lmstudio path, tools are ALWAYS attached — these hosted models handle
    function-calling correctly (no refusal quirk).
    """
    import json as _json

    from openai import AsyncOpenAI

    prefix, default_base = OPENAI_COMPAT_HOSTED[provider]
    client = AsyncOpenAI(
        base_url=os.getenv(f"{prefix}_BASE_URL", default_base),
        api_key=os.getenv(f"{prefix}_API_KEY", ""),
    )
    model = os.getenv(f"{prefix}_MODEL", "")
    kwargs: dict = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": True,
    }
    if tools:
        kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
    resp = await client.chat.completions.create(**kwargs)
    pending: dict[int, dict] = {}
    async for chunk in resp:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice or not choice.delta:
            continue
        d = choice.delta
        if d.content:
            yield {"kind": "text", "text": d.content}
        for tc in d.tool_calls or []:
            slot = pending.setdefault(tc.index, {"name": "", "args": ""})
            if tc.function and tc.function.name:
                slot["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                slot["args"] += tc.function.arguments
    for slot in pending.values():
        try:
            args = _json.loads(slot["args"] or "{}")
        except Exception:
            args = {}
        yield {"kind": "tool_call", "name": slot["name"], "arguments": args}


async def _stream_lmstudio(messages, tools) -> AsyncIterator[dict]:
    """Same OpenAI-compatible streaming, pointed at local LM Studio.

    NOTE (model quirk, liquid/lfm2.5-1.2b): merely ATTACHING a tools array —
    even when nothing needs one — flips this small model into a refusal mode
    on coding/writing prompts, and makes it answer math from weights instead
    of calling the calculator. So tools are attached ONLY on the tool-result
    follow-up turn ("Tool X returned: ..."); the FIRST turn is always
    tool-free, and a keyword router decides server-side whether a tool is
    actually needed (see router.py). The model therefore never sees a tools
    array on the deciding turn — no refusal, no lazy math.
    """
    import json as _json

    from openai import AsyncOpenAI

    last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
    # reply-shaped tool follow-ups ("Tool X returned: ...") never need tools
    needs_tools = last_user.startswith("Tool ")
    client = AsyncOpenAI(
        base_url=os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key=os.getenv("LMSTUDIO_API_KEY", "lm-studio"),
    )
    model = os.getenv("LMSTUDIO_MODEL", "liquid/lfm2.5-1.2b")
    kwargs: dict = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": True,
    }
    if needs_tools:
        kwargs["tools"] = (
            [{"type": "function", "function": t} for t in tools] if tools else None
        )
    resp = await client.chat.completions.create(**kwargs)
    pending: dict[int, dict] = {}
    async for chunk in resp:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice or not choice.delta:
            continue
        d = choice.delta
        if d.content:
            yield {"kind": "text", "text": d.content}
        for tc in d.tool_calls or []:
            slot = pending.setdefault(tc.index, {"name": "", "args": ""})
            if tc.function and tc.function.name:
                slot["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                slot["args"] += tc.function.arguments
    for slot in pending.values():
        try:
            args = _json.loads(slot["args"] or "{}")
        except Exception:
            args = {}
        yield {"kind": "tool_call", "name": slot["name"], "arguments": args}
