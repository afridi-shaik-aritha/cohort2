"""Q6 multi-paper answer generation — grounded, cited, attributed per paper.

Same streaming contract and NOT_IN_PAPER gate as Q3; two differences:
  * the prompt labels every block with its paper title and instructs per-claim
    attribution — no silent blending across papers;
  * the system prompt carries NO access-control language. Access was enforced
    structurally in scope.py; the model never sees foreign content, so there is
    nothing for it to hide.

Every turn is one Langfuse trace (q6.multi_paper_qa) with the scoped paper_ids
in metadata, so Q5's cost alert (name = answer) also sees Q6 traffic.
"""
import asyncio
import datetime as _dt

from app.q2_paper_inference import storage
from app.q3_rag_qa import analyzer as q3_analyzer
from app.q6_multi_paper_rag import retrieval
from app.q6_multi_paper_rag.scope import Scope
from app.shared import langfuse_client as lf
from app.shared.llm_text import Message, estimate_tokens, provider_model, stream_text
from app.shared.protocol import (
    citation_event,
    done_event,
    error_event,
    token_event,
)

__all__ = ["answer_events_multi"]

_MAX_CONTEXT_CHARS = 14_000  # a little more room: blocks now carry titles

_SYSTEM_PROMPT = (
    "You answer questions about a small set of academic papers. You are given "
    "numbered context blocks; each block names the PAPER it came from and the "
    "section. Rules:\n"
    "1. Answer ONLY from the context blocks — never from background knowledge.\n"
    "2. If the context does not contain the answer, reply with exactly "
    "`NOT_IN_PAPER` and nothing else.\n"
    "3. Cite sources inline by appending [1], [2] … matching the block numbers "
    "for each claim.\n"
    "4. When blocks come from different papers, keep their claims attributed: "
    "never merge two papers' results into one claim — cite each paper's block "
    "for that paper's contribution.\n"
    "5. Be concise (3–6 sentences unless the question needs more)."
)


def _user_prompt(question: str, blocks: list[dict]) -> str:
    parts = [f"Question: {question}", "", "Context blocks from the paper(s):"]
    budget = _MAX_CONTEXT_CHARS
    for b in blocks:
        text = b["text"]
        if len(text) > budget:
            text = text[:budget] + " …"
        budget -= len(text)
        parts.append(f"\n[{b['position']}] ({b['paper_title']} — {b['label']})\n{text}")
        if budget <= 0:
            break
    return "\n".join(parts)


async def answer_events_multi(
    scope: Scope, question: str, cancel_ev
) -> "asyncio.AsyncIterator[dict]":
    """Citation/token/done payloads for one multi-paper Q&A turn."""
    provider, model = provider_model()
    owner_id = scope.owner_id

    with lf.multi_qa_trace(
        owner_id=owner_id,
        provider=provider,
        model=model,
        paper_ids=scope.paper_ids,
        metadata={"question": question[:500], "scoped_papers": scope.paper_ids},
    ) as client:
        with lf.observation(
            name="retrieve",
            as_type="span",
            input={"question": question[:500], "scope": scope.paper_ids},
        ) as retrieve_span:
            result = await retrieval.retrieve_multi(scope, question)
            retrieve_span.update(
                output={
                    "path": result.path,
                    "top_score": result.top_score,
                    "blocks": len(result.context_blocks),
                    "papers": [p["paper_id"] for p in result.papers_cited],
                },
                metadata={
                    "owner_id": owner_id,
                    "scoped_paper_ids": scope.paper_ids,
                    "papers_cited": result.papers_cited,
                },
            )

        citations = result.citations
        for c in citations:
            yield citation_event(
                c["position"], c["label"], c["snippet"],
                paper_id=c["paper_id"], paper_title=c["paper_title"],
            )

        if result.path == "refused" or not scope.records:
            yield token_event(q3_analyzer.REFUSAL_MESSAGE)
            yield done_event("refused", None, refused=True, citations=citations,
                             papers_cited=result.papers_cited)
            _persist(scope, question, q3_analyzer.REFUSAL_MESSAGE, [], refused=True,
                     papers=[], usage=None)
            if client is not None:
                lf.flush()
            return

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
                    "owner_id": owner_id,
                    "scoped_paper_ids": scope.paper_ids,
                    "papers_cited": result.papers_cited,
                },
            ) as gen_span:
                pending = ""
                streamed = False
                async for item in stream_text(messages, max_tokens=2_000):
                    if cancel_ev.is_set():
                        stop_reason = "cancelled"
                        break
                    if item["kind"] == "text":
                        answer_chars.append(item["text"])
                        if streamed:
                            yield token_event(item["text"])
                            continue
                        pending += item["text"]
                        if q3_analyzer._could_be_marker(pending):
                            continue
                        streamed = True
                        yield token_event(pending)
                        pending = ""
                    elif item["kind"] == "usage":
                        usage = item.get("usage")
                if not streamed and pending.strip():
                    if not q3_analyzer._is_refusal(pending):
                        yield token_event(pending)
                        streamed = True
                answer = "".join(answer_chars)
                refused = (not streamed) and q3_analyzer._is_refusal(answer)
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
                        "papers_cited": result.papers_cited,
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
            yield token_event(q3_analyzer.REFUSAL_MESSAGE)
            yield done_event("refused", usage, refused=True, citations=[],
                             papers_cited=result.papers_cited)
            _persist(scope, question, q3_analyzer.REFUSAL_MESSAGE, [], refused=True,
                     papers=[], usage=usage)
        else:
            yield done_event(stop_reason, usage, refused=False, citations=citations,
                             papers_cited=result.papers_cited)
            _persist(scope, question, answer, citations, refused=False,
                     papers=result.papers_cited, usage=usage)
        if client is not None:
            lf.flush()


def _persist(
    scope: Scope,
    question: str,
    answer: str,
    citations: list[dict],
    *,
    refused: bool,
    papers: list[dict],
    usage: dict | None,
) -> None:
    """Append the turn to the papers the answer drew from (or all scoped records
    when nothing was cited), keeping each paper's qa_history self-contained."""
    targets = papers or [{"paper_id": pid} for pid in scope.paper_ids]
    turn = {
        "question": question,
        "answer": answer,
        "citations": citations,
        "refused": refused,
        "path": "multi",
        "papers_cited": papers,
        "usage": usage,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    for t in targets:
        rec = storage.load_paper(t["paper_id"])
        if rec is None:
            continue
        history = rec.setdefault("qa_history", [])
        history.append(turn)
        rec["qa_history"] = history[-100:]
        storage.save_paper(rec)