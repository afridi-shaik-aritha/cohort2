"""Plain-text streaming completion with best-effort usage capture (Q2+).

Separate from `llm_client.py` on purpose: Q1's tool-calling streamer is approved
and stays untouched. This module does the simpler thing Q2-Q6 need — stream text
and report provider token usage so Langfuse can compute cost per generation.

Yields:  {"kind": "text", "text": str} …then exactly one…
         {"kind": "usage", "usage": {"input": n, "output": m, "total": t} | None}

Usage is never fabricated: when a provider does not report it (e.g. LM Studio),
`usage` is None and callers record `estimate_tokens()` separately, labelled as an
estimate.
"""
import asyncio
import os
import time
from typing import AsyncIterator, Optional

import httpx

from app.shared.llm_client import OPENAI_COMPAT_HOSTED, Message, get_provider

__all__ = ["Message", "stream_text", "provider_model", "estimate_tokens", "input_char_budget"]

# Default per-call paper budget (chars). The spec's 40k assumes a comfortable
# context; `input_char_budget()` may lower it to fit the loaded local model.
DEFAULT_INPUT_CHARS = 40_000
_MAX_TOKENS_PER_SECTION = 1_200  # keep in sync with the analyzer
_CTX_SAFETY_TOKENS = 256
_BUDGET_TTL_SECONDS = 60.0
_budget_cache: tuple[float, int] | None = None


def provider_model() -> tuple[str, str]:
    """(provider, model) for trace metadata / UI chips."""
    p = get_provider()
    if p == "lmstudio":
        return p, os.getenv("LMSTUDIO_MODEL", "liquid/lfm2.5-1.2b")
    if p == "openai":
        return p, os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if p == "anthropic":
        return p, os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
    if p in OPENAI_COMPAT_HOSTED:
        prefix, _ = OPENAI_COMPAT_HOSTED[p]
        return p, os.getenv(f"{prefix}_MODEL", "")
    return "mock", "mock-streamer"


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — always labelled as an estimate."""
    return max(1, len(text) // 4)


async def input_char_budget() -> int:
    """Per-call paper budget in chars, adaptive to the loaded model's context.

    Local servers (LM Studio) may load models with small context windows
    (e.g. 8192 tokens) — a fixed 40k-char prompt would be rejected outright.
    For LM Studio we probe the REST API for the loaded instance's
    context_length and size the budget as (ctx − reserved) tokens × ~4
    chars/token, clamped to [8k, 40k]. Probe result is cached for 60s so the
    router and analyzer agree without hammering the server. Probe failure →
    the spec default; a too-small context still degrades to per-section
    errors rather than breaking the run.
    """
    global _budget_cache
    provider, _ = provider_model()
    if provider != "lmstudio":
        return DEFAULT_INPUT_CHARS
    now = time.monotonic()
    if _budget_cache is not None and now - _budget_cache[0] < _BUDGET_TTL_SECONDS:
        return _budget_cache[1]
    ctx = await _probe_lmstudio_context()
    if ctx is None:
        return DEFAULT_INPUT_CHARS
    usable = ctx - _MAX_TOKENS_PER_SECTION - _CTX_SAFETY_TOKENS
    budget = max(8_000, min(DEFAULT_INPUT_CHARS, usable * 4))
    _budget_cache = (now, budget)
    return budget


async def _probe_lmstudio_context() -> Optional[int]:
    """context_length of the loaded LM Studio model, or None if undiscoverable."""
    base = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
    root = base.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    model = os.getenv("LMSTUDIO_MODEL", "liquid/lfm2.5-1.2b")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{root}/api/v1/models")
            resp.raise_for_status()
            for m in resp.json().get("models", []):
                if m.get("key") != model:
                    continue
                for inst in m.get("loaded_instances", []):
                    ctx = (inst.get("config") or {}).get("context_length")
                    if ctx:
                        return int(ctx)
    except Exception:
        return None
    return None


async def stream_text(
    messages: list[Message],
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> AsyncIterator[dict]:
    provider, model = provider_model()
    if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        async for item in _stream_anthropic(messages, model, max_tokens, temperature):
            yield item
        return
    if provider != "mock" and _key_for(provider):
        async for item in _stream_openai_compat(messages, provider, model, max_tokens, temperature):
            yield item
        return
    async for item in _stream_mock(messages):
        yield item


def _key_for(provider: str) -> str:
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY", "")
    if provider == "lmstudio":
        return os.getenv("LMSTUDIO_API_KEY", "lm-studio")  # local: always "present"
    if provider in OPENAI_COMPAT_HOSTED:
        prefix, _ = OPENAI_COMPAT_HOSTED[provider]
        return os.getenv(f"{prefix}_API_KEY", "")
    return ""


def _client_kwargs(provider: str) -> dict:
    if provider == "lmstudio":
        return {
            "base_url": os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
            "api_key": os.getenv("LMSTUDIO_API_KEY", "lm-studio"),
        }
    if provider in OPENAI_COMPAT_HOSTED:
        prefix, default_base = OPENAI_COMPAT_HOSTED[provider]
        return {
            "base_url": os.getenv(f"{prefix}_BASE_URL", default_base),
            "api_key": os.getenv(f"{prefix}_API_KEY", ""),
        }
    return {}  # openai: SDK defaults


def _supports_usage_request(provider: str) -> bool:
    """Whether the provider accepts stream_options={include_usage: True}.

    Verified live on LM Studio 0.3.x (2026-09-17): accepted, and the final
    chunk carries real prompt/completion token counts — so local models DO
    report usage to Langfuse. If a future provider rejects the field, exclude
    it here; usage then falls back to None + estimate_tokens()."""
    return True


async def _stream_openai_compat(
    messages: list[Message],
    provider: str,
    model: str,
    max_tokens: Optional[int],
    temperature: Optional[float],
) -> AsyncIterator[dict]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(**_client_kwargs(provider))
    kwargs: dict = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": True,
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    if _supports_usage_request(provider):
        kwargs["stream_options"] = {"include_usage": True}

    usage: Optional[dict] = None
    try:
        resp = await client.chat.completions.create(**kwargs)
        async for chunk in resp:
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage = {
                    "input": getattr(u, "prompt_tokens", 0),
                    "output": getattr(u, "completion_tokens", 0),
                    "total": getattr(u, "total_tokens", 0),
                }
            choice = chunk.choices[0] if chunk.choices else None
            if choice and choice.delta and choice.delta.content:
                yield {"kind": "text", "text": choice.delta.content}
    except Exception as e:
        raise RuntimeError(f"{provider} request failed: {e}") from e
    yield {"kind": "usage", "usage": usage}


async def _stream_anthropic(
    messages: list[Message],
    model: str,
    max_tokens: Optional[int],
    temperature: Optional[float],
) -> AsyncIterator[dict]:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    system, convo = "", []
    for m in messages:
        if m.role == "system":
            system += m.content
        else:
            convo.append({"role": m.role, "content": m.content})
    kwargs: dict = {"model": model, "max_tokens": max_tokens or 2048, "messages": convo}
    if system:
        kwargs["system"] = system
    if temperature is not None:
        kwargs["temperature"] = temperature
    usage: Optional[dict] = None
    try:
        async with client.messages.stream(**kwargs) as stream:
            async for chunk in stream:
                if chunk.type == "content_block_delta" and getattr(chunk.delta, "text", None):
                    yield {"kind": "text", "text": chunk.delta.text}
            final = await stream.get_final_message()
        u = getattr(final, "usage", None)
        if u is not None:
            usage = {
                "input": getattr(u, "input_tokens", 0),
                "output": getattr(u, "output_tokens", 0),
                "total": getattr(u, "input_tokens", 0) + getattr(u, "output_tokens", 0),
            }
    except Exception as e:
        raise RuntimeError(f"anthropic request failed: {e}") from e
    yield {"kind": "usage", "usage": usage}


_MOCK_SECTION_TEXT = {
    "prerequisite": (
        "## Prerequisite concepts\n\n"
        "- **Attention mechanisms** — the paper's core building block; every claim "
        "about performance depends on how attention is formulated.\n"
        "- **Sequence-to-sequence models** — the baseline this work replaces, which "
        "frames translation as encoder-decoder recurrence.\n"
        "- **Tokenization / BPE** — how text becomes the discrete symbols the model consumes.\n"
        "- **Transfer learning** — pretrain-then-finetune, the setting results are reported under.\n"
        "- **BLEU** — the headline evaluation metric used throughout.\n"
    ),
    "intuition": (
        "## Intuition\n\n"
        "Imagine reading a sentence while allowed to glance at every other word at "
        "once, deciding on the fly which words matter for the one you're producing. "
        "That is the paper's central move: instead of passing meaning along a chain "
        "one step at a time, let every position look directly at every other position "
        "and weigh what it finds.\n\n"
        "The practical payoff: information no longer has to survive a long journey to "
        "stay relevant — distance stops being a bottleneck, and the computation can be "
        "run in parallel rather than sequentially.\n"
    ),
    "technical": (
        "## Technical summary\n\n"
        "Demo-mode (mock) technical summary. In live mode the model writes 250–400 "
        "words naming the proposed method, architecture, datasets, evaluation metrics, "
        "and headline results with their numbers, assuming the reader knows the field.\n\n"
        "Structure: the method replaces recurrence with a stacked attention "
        "formulation, trained end-to-end on parallel corpora, evaluated with standard "
        "automated metrics against recurrent baselines.\n"
    ),
    "summary": (
        "## Summary\n\n"
        "A concise, abstract-register summary of the uploaded paper: the problem it "
        "addresses, the approach taken, the evidence presented, and the conclusion the "
        "authors draw. Demo mode produces this canned text so the full pipeline — "
        "extraction, per-section streaming, storage, and tracing — can be exercised "
        "without model credentials.\n"
    ),
}


async def _stream_mock(messages: list[Message]) -> AsyncIterator[dict]:
    """Deterministic demo text, selected by section so the four panels differ."""
    last = next((m.content for m in reversed(messages) if m.role == "user"), "").lower()
    key = next((k for k in _MOCK_SECTION_TEXT if k in last), "summary")
    for word in _MOCK_SECTION_TEXT[key].split(" "):
        yield {"kind": "text", "text": word + " "}
        await asyncio.sleep(0.012)
    yield {"kind": "usage", "usage": None}