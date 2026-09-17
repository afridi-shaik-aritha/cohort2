"""Q3 QA generation: grounded, cited answers streamed as SSE events.

Event order (docs/specs/q3-spec.md "API contract"):
  run → citation* (before tokens, so source chips render early) → token* →
  done{stop_reason, usage, refused, citations}

The router owns run creation + run_event (Q2 pattern); this module consumes a
cancel event and yields citation/token/done payloads.

Grounding is two-layer (spec §3):
  1. retrieval gate — `retrieval.retrieve()` returns path="refused" when no
     index, no chunks, or top cosine < REFUSAL_FLOOR; we emit the honest
     refusal without calling the model at all.
  2. generation gate — the prompt requires answering ONLY from the provided
     context blocks and to emit exactly `NOT_IN_PAPER` when they don't contain
     the answer. The marker may already have streamed as tokens, so `done`
     carries refused=true and the frontend swaps the streamed body for the
     friendly refusal message.

Every turn is one Langfuse trace (q3.paper_qa) with a `retrieve` span and an
`answer` generation; refusals are traced too (refused=true) so Q5's alerting
sees them.
"""
import asyncio
import datetime as _dt
import re

from app.q2_paper_inference import storage
from app.q3_rag_qa import retrieval
from app.shared import langfuse_client as lf
from app.shared.llm_text import Message, estimate_tokens, provider_model, stream_text
from app.shared.protocol import (
    citation_event,
    done_event,
    error_event,
    token_event,
)

__all__ = ["answer_events", "REFUSAL_MESSAGE"]

REFUSAL_MESSAGE = "I don't have enough information in this paper to answer that."
_MARKER = "NOT_IN_PAPER"
_MARKER_NORM = "NOT IN PAPER"  # _normalize() maps underscores/spaces/hyphens to spaces
_MAX_CONTEXT_CHARS = 12_000  # answer prompt window over the retrieved blocks
_MAX_ANSWER_TOKENS = 2_000  # a full references list needs room (live check hit the cap at 1,200)
_MAX_ANSWER_TOKENS_REFERENCES = 3_500  # listing ~40 entries + reasoning-model overhead

_SYSTEM_PROMPT = (
    "You answer questions about one specific academic paper. You are given "
    "numbered context blocks extracted from that paper. Rules:\n"
    "1. Answer ONLY from the context blocks — never from background knowledge.\n"
    "2. If the context does not contain the answer, reply with exactly "
    "`NOT_IN_PAPER` and nothing else.\n"
    "3. Cite sources inline by appending [1], [2] … matching the block numbers "
    "for each claim you make.\n"
    "4. Be concise (3–6 sentences unless the question needs more, e.g. a "
    "references list)."
)


def _user_prompt(question: str, blocks: list[dict]) -> str:
    parts = [f"Question: {question}", "", "Context blocks from the paper:"]
    budget = _MAX_CONTEXT_CHARS
    for b in blocks:
        text = b["text"]
        if len(text) > budget:
            text = text[:budget] + " …"
        budget -= len(text)
        parts.append(f"\n[{b['position']}] ({b['label']})\n{text}")
        if budget <= 0:
            break
    return "\n".join(parts)


def _normalize(s: str) -> str:
    return re.sub(r"[\s_\-]+", " ", s.strip().upper())


def _is_refusal(answer: str) -> bool:
    """True when the whole answer is the NOT_IN_PAPER marker (any spacing)."""
    return _normalize(answer).startswith(_MARKER_NORM)


def _could_be_marker(pending: str) -> bool:
    """Whether buffered text could still resolve to the marker — i.e. its
    normalized form is a (strict or full) prefix of the normalized marker."""
    norm = _normalize(pending)
    return bool(norm) and _MARKER_NORM.startswith(norm)


async def answer_events(record: dict, question: str, cancel_ev) -> "asyncio.AsyncIterator[dict]":
    """Async generator of citation/token/done payloads for one Q&A turn.

    (run_event is the router's job, mirroring Q2.)
    """
    provider, model = provider_model()
    paper_id = record["paper_id"]
    owner_id = record.get("owner_id", "demo-user")

    with lf.qa_trace(
        paper_id=paper_id,
        title=record.get("title", ""),
        owner_id=owner_id,
        provider=provider,
        model=model,
        metadata={"question": question[:500]},
    ) as client:
        # --- retrieval (traced; the refusal short-circuit is traced too) -----
        with lf.observation(
            name="retrieve",
            as_type="span",
            input={"question": question[:500]},
        ) as retrieve_span:
            result = await retrieval.retrieve(record, question)
            retrieve_span.update(
                output={"path": result.path, "top_score": result.top_score,
                        "blocks": len(result.context_blocks)},
                metadata={
                    "question_type": result.question_type,
                    "top_score": result.top_score,
                    "citations_count": len(result.citations),
                    "refused": result.path == "refused",
                },
            )

        citations = result.citations
        for c in citations:
            yield citation_event(c["position"], c["label"], c["snippet"])

        if result.path == "refused":
            yield token_event(REFUSAL_MESSAGE)
            yield done_event("refused", None, refused=True, citations=citations)
            _persist_turn(record, question, REFUSAL_MESSAGE, citations, refused=True,
                          path=result.path, top_score=result.top_score, usage=None)
            if client is not None:
                lf.flush()
            return

        # --- grounded generation ---------------------------------------------
        # Message objects, not dicts — stream_text accesses .role/.content
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=_user_prompt(question, result.context_blocks)),
        ]
        answer_chars: list[str] = []
        usage: dict | None = None
        stop_reason = "completed"
        try:
            with lf.observation(
                name="answer",
                as_type="generation",
                model=model,
                input=messages,
                metadata={
                    "path": result.path,
                    "question_type": result.question_type,
                    "top_score": result.top_score,
                    "context_chars": sum(len(b["text"]) for b in result.context_blocks),
                },
            ) as gen_span:
                # Prefix-buffered streaming: hold back deltas only while they
                # could still be the leading NOT_IN_PAPER marker, so the raw
                # marker never flickers into the chat. Divergence flushes the
                # buffer and the answer streams live from then on.
                pending = ""
                streamed = False
                async for item in stream_text(
                    messages,
                    max_tokens=(
                        _MAX_ANSWER_TOKENS_REFERENCES
                        if result.path == "references"
                        else _MAX_ANSWER_TOKENS
                    ),
                ):
                    if cancel_ev.is_set():
                        stop_reason = "cancelled"
                        break
                    if item["kind"] == "text":
                        answer_chars.append(item["text"])
                        if streamed:
                            yield token_event(item["text"])
                            continue
                        pending += item["text"]
                        if _could_be_marker(pending):
                            continue  # keep buffering
                        streamed = True
                        yield token_event(pending)
                        pending = ""
                    elif item["kind"] == "usage":
                        usage = item.get("usage")
                if not streamed and pending.strip():
                    # Stream ended while still marker-shaped: refusal iff it IS
                    # the marker; otherwise flush it as the (short) answer.
                    if not _is_refusal(pending):
                        yield token_event(pending)
                        streamed = True
                answer = "".join(answer_chars)
                refused = (not streamed) and _is_refusal(answer)
                gen_span.update(
                    output=answer[:4_000],
                    usage_details=(
                        {"input": usage["input"], "output": usage["output"],
                         "total": usage["total"]}
                        if usage
                        else None
                    ),
                    metadata={
                        "refused": refused,
                        "model_input_estimate": estimate_tokens(messages[1].content),
                    },
                )
        except Exception as e:
            yield error_event(f"generation failed: {str(e)[:200]}", False)
            yield done_event("error", None, refused=False, citations=citations)
            if client is not None:
                lf.flush()
            return

        if stop_reason == "cancelled":
            yield done_event("cancelled", usage, refused=False, citations=citations)
            return
        if refused:
            # The marker was buffered (never streamed) — emit the friendly
            # refusal as the answer text, then done.refused confirms the state.
            yield token_event(REFUSAL_MESSAGE)
            yield done_event("refused", usage, refused=True, citations=[])
            _persist_turn(record, question, REFUSAL_MESSAGE, [], refused=True,
                          path=result.path, top_score=result.top_score, usage=usage)
        else:
            yield done_event(stop_reason, usage, refused=False, citations=citations)
            _persist_turn(record, question, answer, citations, refused=False,
                          path=result.path, top_score=result.top_score, usage=usage)
        if client is not None:
            lf.flush()


def _persist_turn(
    record: dict,
    question: str,
    answer: str,
    citations: list[dict],
    *,
    refused: bool,
    path: str,
    top_score: float | None,
    usage: dict | None,
) -> None:
    """Append the turn to the paper record (chat continuity + Q4/Q5 analytics)."""
    history = record.setdefault("qa_history", [])
    history.append(
        {
            "question": question,
            "answer": answer,
            "citations": citations,
            "refused": refused,
            "path": path,
            "top_score": top_score,
            "usage": usage,
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }
    )
    record["qa_history"] = history[-100:]
    storage.save_paper(record)
