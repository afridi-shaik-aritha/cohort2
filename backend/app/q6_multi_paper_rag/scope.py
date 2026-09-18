"""Q6 scope resolution — THE access-control layer.

Two jobs, both decided BEFORE any retrieval or generation:

1. Owner scoping (structural). `scoped_records(owner_id)` returns only records
   whose `owner_id` matches the requester. The vector-search pool is built from
   these records and nothing else, so a foreign paper cannot be retrieved even
   in principle — this is the metadata-filter equivalent of
   `WHERE owner_id = current_user`, applied where the chunks are gathered.

2. Disambiguation. "How was this tested?" is ambiguous across papers. Fixed
   order: explicit selector scope → title mention in the question → all of the
   user's papers. A question that names a paper NOT among the accessible titles
   refuses immediately with ACCESS_REFUSAL — decided from the user's own
   catalog only, so the refusal reveals nothing about other users' libraries
   (an unowned title and a nonexistent title are indistinguishable).
"""
import re

from app.q2_paper_inference import storage
from app.q6_multi_paper_rag import ACCESS_REFUSAL

__all__ = ["Denial", "Scope", "resolve_scope", "scoped_records"]

# A title mention must clear this token-overlap bar to count as the same paper
# (guards against a passing word matching a one-word title).
TITLE_MATCH_RATIO = 0.6

_TITLEY_RE = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{6,160})[\"'“”‘’]")
# "what does <X> say", "in <X>", "from the paper <X>" — a target-paper shape.
_SAYS_ABOUT_RE = re.compile(
    r"\b(?:does|did)\b[^?.!]*\bsay\b|\bin\b\s+(?:the\s+)?(?:paper|article)\b"
    r"|\bfrom\b\s+(?:the\s+)?(?:paper|article)\b|\bsummar(?:y|ise|ize)\b\s+(?:of|the)\b",
    re.I,
)


class Denial(Exception):
    """Raised when the question names a paper outside the requester's scope."""

    def __init__(self, message: str = ACCESS_REFUSAL):
        super().__init__(message)
        self.message = message


class Scope:
    """The resolved retrieval scope: owner-filtered records + metadata."""

    def __init__(self, owner_id: str, records: list[dict], paper_ids: list[str] | None):
        self.owner_id = owner_id
        self.records = records            # owner-filtered, selector-narrowed
        self.requested_ids = paper_ids    # explicit selector, or None

    @property
    def paper_ids(self) -> list[str]:
        return [r["paper_id"] for r in self.records]


def scoped_records(owner_id: str) -> list[dict]:
    """The structural access filter — the ONLY source of retrieval candidates.

    Equivalent to `WHERE owner_id = current_user` on a vector-DB metadata
    filter: a record owned by anyone else is never returned, so its chunks can
    never enter the pool that the vector search scores against.
    """
    if not owner_id:
        return []
    return [
        rec
        for rec in storage.all_records()
        if rec.get("owner_id") == owner_id
    ]


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def _title_match_ratio(question_tokens: set[str], title: str) -> float:
    t = _tokens(title)
    if not t:
        return 0.0
    return len(question_tokens & t) / len(t)


def _mentioned_record(question: str, records: list[dict]) -> dict | None:
    """Best accessible paper whose title the question names, or None.

    Two signals: a quoted phrase equal/overlapping the title, or strong token
    overlap. Only the requester's OWN papers are consulted — an unowned title
    and a nonexistent title are indistinguishable here by construction.
    """
    q_tokens = _tokens(question)
    best, best_ratio = None, 0.0
    for rec in records:
        title = rec.get("title") or ""
        if not title:
            continue
        ratio = _title_match_ratio(q_tokens, title)
        quoted = any(
            q.strip().lower() == title.strip().lower()
            for q in _TITLEY_RE.findall(question)
        )
        if quoted:
            ratio = max(ratio, 1.0)
        if ratio > best_ratio:
            best, best_ratio = rec, ratio
    if best is not None and best_ratio >= TITLE_MATCH_RATIO:
        return best
    return None


def _names_inaccessible_paper(question: str, records: list[dict]) -> bool:
    """True when the question names a *specific* paper that is not accessible.

    Detected without consulting foreign records, so the refusal leaks nothing:
    - a quoted title-like phrase (≥3 tokens) matching none of the user's
      papers, or
    - a "what does <title-case phrase> say" shape whose phrase does not match
      an accessible title.

    An unowned title and a nonexistent title are indistinguishable here — that
    is the point (no existence leak).
    """
    for phrase in _TITLEY_RE.findall(question):
        if len(_tokens(phrase)) >= 3 and _mentioned_record(phrase, records) is None:
            return True
    m = re.search(
        r"\bdoes\b\s+(?P<phrase>[^?.!]{6,160}?)\s+\b(?:say|said|claim|propose|report)\b",
        question, re.I,
    )
    if m and not _mentioned_record(m.group(1), records):
        words = m.group(1).strip().strip("\"'“”‘’").split()
        caps = sum(1 for w in words if w[:1].isupper())
        if len(words) >= 3 and caps >= len(words) * 0.6:
            return True
    return False


def resolve_scope(
    owner_id: str,
    question: str,
    paper_ids: list[str] | None = None,
) -> Scope:
    """Owner filter → selector filter → title-mention filter.

    Raises Denial when the question targets a paper the user cannot access.
    """
    records = scoped_records(owner_id)  # §4: the structural filter, always first

    if paper_ids is not None:
        wanted = set(paper_ids)
        records = [r for r in records if r["paper_id"] in wanted]
        if not records:
            # selector named only papers this owner does not have — same
            # refusal as naming a foreign title: no existence leak.
            raise Denial()

    mentioned = _mentioned_record(question, records)
    if mentioned is not None:
        return Scope(owner_id=owner_id, records=[mentioned], paper_ids=paper_ids)

    if not records or _names_inaccessible_paper(question, records):
        # Question targets a specific paper outside this owner's catalog
        # (foreign owner, bad selector, or deleted) — deny at the scope layer;
        # retrieval and the model never run.
        raise Denial()

    return Scope(owner_id=owner_id, records=records, paper_ids=paper_ids)
