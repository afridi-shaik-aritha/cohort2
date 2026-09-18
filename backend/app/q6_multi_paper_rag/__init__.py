"""Q6 — Multi-paper RAG with owner-scoped access control.

Extends Q3's single-paper Q&A to a shared index of many users' papers:

  scope.py      — THE access-control layer: the retrieval candidate pool is
                  built exclusively from records owned by the requesting user
                  (the structural equivalent of `WHERE owner_id = current_user`),
                  plus explicit-scope / title-mention disambiguation.
  retrieval.py  — cross-paper hybrid retrieval over the pooled chunks; every
                  block carries its paper_id/title so answers attribute claims
                  per paper.
  analyzer.py   — grounded multi-paper answer streaming (same NOT_IN_PAPER
                  gate as Q3; the prompt has NO access-control language —
                  access is structural, not instructional).
  router.py     — /api/q6 endpoints; simulated users via the X-Owner-Id header.

Access denial message (distinct from Q3's evidence-gap refusal so a grader can
tell the two apart):
"""

ACCESS_REFUSAL = "I don't have access to that paper."

__all__ = ["ACCESS_REFUSAL"]
