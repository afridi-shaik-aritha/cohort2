"""Q2 analyzer — four sequential section generations with tracing + map-reduce.

Emits protocol payloads (dicts ready for `format_sse`):
  section_start → token* → section_done   (×4, in SECTIONS order)
  then done{stop_reason, totals}
Cancellation is cooperative via the `cancel_ev` shared with the router's
DELETE /runs/{id}/cancel endpoint.
"""
import asyncio
import time
from typing import AsyncIterator, Optional

from app.q2_paper_inference import prompts
from app.shared import langfuse_client as lf
from app.shared.llm_text import (
    DEFAULT_INPUT_CHARS,
    estimate_tokens,
    input_char_budget,
    provider_model,
    stream_text,
)
from app.shared.protocol import (
    done_event,
    section_done as section_done_event,
    section_start as section_start_event,
    section_token,
)

# Spec ceiling; the per-call budget actually used is adaptive — see
# `llm_text.input_char_budget()` (a loaded 8k-ctx LM Studio model gets ~27k).
MAX_INPUT_CHARS = DEFAULT_INPUT_CHARS
CHUNK_SIZE = 8_000
CHUNK_OVERLAP = 1_000
_DIGEST_FALLBACK_CHARS = 1_500


def strategy_for(text: str) -> str:
    return "single_pass" if len(text) <= MAX_INPUT_CHARS else "map_reduce"


async def analyze_events(record: dict, *, cancel_ev: asyncio.Event) -> AsyncIterator[dict]:
    """Yield SSE payloads for one analyze run. Persists each section as it finishes."""
    from app.q2_paper_inference import storage

    provider, model = provider_model()
    text = record.get("text", "")
    title = record.get("title", "")
    authors = record.get("authors", [])

    usage_total = {"input": 0, "output": 0, "total": 0}
    usage_reported = False
    budget = await input_char_budget()
    # Effective strategy uses the adaptive budget (a small-context local model
    # gets map-reduce earlier) — never silent head-truncation of a full paper.
    strategy = "single_pass" if len(text) <= budget else "map_reduce"

    with lf.analysis_trace(
        paper_id=record.get("paper_id", ""),
        title=title,
        owner_id=record.get("owner_id", storage.DEFAULT_OWNER),
        provider=provider,
        model=model,
        metadata={
            "pages": record.get("pages", 0),
            "chars": record.get("chars", 0),
            "strategy": strategy,
            "sections": prompts.SECTION_KEYS,
        },
    ):
        with lf.observation(
            name="extract_pdf",
            as_type="span",
            metadata={
                "pages": record.get("pages", 0),
                "chars": record.get("chars", 0),
                "strategy": strategy,
                "warnings": (record.get("extraction") or {}).get("warnings", []),
            },
        ) as span:
            span.update(
                output=f"{record.get('chars', 0)} chars from {record.get('pages', 0)} pages",
            )

        context = text
        if strategy == "map_reduce":
            digests, digest_usage = await _digest_phase(record, model, cancel_ev=cancel_ev)
            if digests is None:  # cancelled during the map phase
                yield done_event("cancelled", usage_total if usage_reported else None)
                lf.flush()
                return
            context = digests
            if digest_usage:
                usage_reported = True
                for k in usage_total:
                    usage_total[k] += digest_usage.get(k, 0)

        context = context[:budget]

        for index, section in enumerate(prompts.SECTIONS):
            if cancel_ev.is_set():
                yield done_event("cancelled", usage_total if usage_reported else None)
                lf.flush()
                return
            key = section["key"]
            yield section_start_event(key, section["label"], index, len(prompts.SECTIONS))
            started = time.monotonic()
            collected: list[str] = []
            usage: Optional[dict] = None
            with lf.observation(
                name=f"section:{key}",
                as_type="generation",
                model=model,
                input={
                    "title": title,
                    "instruction": section["instruction"],
                    "context_chars": len(context),
                },
            ) as gen:
                messages = prompts.build_messages(
                    key, title=title, authors=authors, context=context
                )
                try:
                    async for item in stream_text(messages, max_tokens=1_200):
                        if item["kind"] == "text":
                            if cancel_ev.is_set():
                                break
                            collected.append(item["text"])
                            yield section_token(key, item["text"])
                        else:
                            usage = item.get("usage")
                except Exception as e:
                    yield {
                        "type": "error",
                        "data": {"message": f"{key}: {e}", "recoverable": True},
                    }
                output = _strip_code_fence("".join(collected))
                gen.update(output=output, usage_details=usage or None)

            if usage:
                usage_reported = True
                for k in usage_total:
                    usage_total[k] += usage.get(k, 0)

            duration_ms = int((time.monotonic() - started) * 1000)
            yield section_done_event(key, len(output), usage, duration_ms)

            # persist incrementally so a cancelled run keeps finished sections
            record.setdefault("sections", {})[key] = output
            record.setdefault("usage", {})[key] = {
                "usage": usage,
                "estimated_tokens": None if usage else estimate_tokens(output),
                "duration_ms": duration_ms,
            }
            record["extraction"] = {
                "strategy": strategy,
                "warnings": (record.get("extraction") or {}).get("warnings", []),
            }
            storage.save_paper(record)

            if cancel_ev.is_set():
                yield done_event("cancelled", usage_total if usage_reported else None)
                lf.flush()
                return

    yield done_event("completed", usage_total if usage_reported else None)
    lf.flush()


def _strip_code_fence(text: str) -> str:
    """Small models sometimes wrap the whole answer in a ```markdown fence;
    the contract says markdown only, so strip a wrapping fence if present."""
    s = text.strip()
    if s.startswith("```") and "\n" in s:
        body = s.split("\n", 1)[1]
        if body.rstrip().endswith("```"):
            return body.rstrip()[: -3].rstrip()
    return s


async def _digest_phase(
    record: dict, model: str, *, cancel_ev: asyncio.Event
) -> tuple[Optional[str], Optional[dict]]:
    """Map phase: digest every chunk, traced as `chunk_digest:<n>`. Returns
    (concatenated digests capped at MAX_INPUT_CHARS, summed usage|None), or
    (None, None) when cancelled. The map phase can run for minutes on small
    local models — without a cancel check here, an abandoned run would keep
    burning the model until every chunk was digested."""
    text = record.get("text", "")
    chunks = prompts.chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
    digests: list[str] = []
    usage_total = {"input": 0, "output": 0, "total": 0}
    any_usage = False
    for i, chunk in enumerate(chunks):
        if cancel_ev.is_set():
            return None, None
        collected: list[str] = []
        usage: Optional[dict] = None
        with lf.observation(
            name=f"chunk_digest:{i}",
            as_type="generation",
            model=model,
            input={"chars": len(chunk), "index": i, "total": len(chunks)},
        ) as gen:
            try:
                async for item in stream_text(
                    prompts.build_digest_messages(chunk, index=i, total=len(chunks)),
                    max_tokens=500,
                ):
                    if item["kind"] == "text":
                        if cancel_ev.is_set():
                            break
                        collected.append(item["text"])
                    else:
                        usage = item.get("usage")
            except Exception:
                pass
            digest = "".join(collected).strip()
            gen.update(output=digest, usage_details=usage or None)
        if cancel_ev.is_set():
            return None, None
        if usage:
            any_usage = True
            for k in usage_total:
                usage_total[k] += usage.get(k, 0)
        digests.append(digest or chunk[:_DIGEST_FALLBACK_CHARS])
    return "\n\n".join(digests)[:MAX_INPUT_CHARS], (usage_total if any_usage else None)
