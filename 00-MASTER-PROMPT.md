# Master Prompt — paste this as your first message, with the assignment PDF attached
# and this whole package (00–06 files + /docs skeletons) dropped into the repo root
# before you start the session.

You have superpowers (obra/superpowers) installed. Use it as intended: brainstorming
→ spec → plan → execution, skill-announced, with mandatory human checkpoints. Do not
skip steps to save time. This is a 6-question assignment PDF (attached) building an
"AI Fundamentals to AI Engineer" system. Read the whole PDF first before doing anything.

This repo already contains scaffolding you must use, not replace:
- /docs/specs/*.md — spec skeletons, one per question, with the required sections
  pre-listed from the assignment. Fill them in during brainstorming; don't restructure
  them unless something genuinely doesn't fit.
- /docs/design/design-tokens.md — the locked visual design system (colors, type,
  spacing, component rules). Frontend work must use these tokens, not invent new ones.
- /docs/q4-writeup-template.md, /docs/q5-alert-proof-template.md — fill these in when
  you reach Q4/Q5.
- README.md — repo overview, fill in setup instructions as you build.

===========================================================
GLOBAL RULES (apply to every question, no exceptions)
===========================================================

1. ONE QUESTION AT A TIME, STRICT GATE.
   - Work on exactly one question (Q1 → Q2 → Q3 → Q4 → Q5 → Q6), in order.
   - For each question: brainstorm/spec it (fill in /docs/specs/q<N>-spec.md), plan it,
     build it, run the PDF's own "Working system check" yourself, then STOP and tell
     me explicitly: "Q<N> is implemented and ready for you to test. Here's how to run
     it and what to check: [...]". Then WAIT for my explicit go-ahead.
   - Do NOT start the next question's spec, plan, or code until I confirm the current
     one works. If I report something broken, fix it and re-request confirmation —
     do not proceed to the next question in the meantime.
   - Q4 is a written/research deliverable, not a UI feature — still stop and show me
     the writeup (in /docs/q4-writeup-template.md) before moving to Q5, since Q5's
     threshold depends on Q4's numbers.
   - Q6 extends Q2/Q3's ingestion pipeline. Don't silently refactor already-approved
     Q2/Q3 code to fit Q6 — call out any breaking changes explicitly before making
     them, and confirm Q2/Q3's working system checks still pass afterward.

2. SINGLE REPO, ONE RUNNABLE SYSTEM.
   - Everything lives in this one repository. Backend: FastAPI (Python), matching my
     existing stack (RAG pipelines, embeddings, FastAPI) — reuse conventions rather
     than inventing a new stack per question.
   - Frontend: one app, not six. A landing/home page lists all 6 questions as cards
     (title + one-line description + status badge: "Not built" / "In progress" /
     "Ready to test" / "Done"). Clicking a card routes to that question's own page
     inside the same app (shared shell, nav, design system). Don't build 6 separate
     frontend projects.
   - Update a card's status as you go. Don't pre-link cards for unbuilt questions —
     show them disabled/greyed with "Coming soon" until that question is actually
     built and I've approved it as done.
   - Follow the repo structure in README.md's "Repository layout" section.

3. DESIGN: Anthropic-style crisp UI/UX.
   - Before writing any frontend code, load your frontend-design skill/guidance AND
     read /docs/design/design-tokens.md. Apply both deliberately — don't default to
     generic Bootstrap/Material look, and don't deviate from the locked tokens without
     flagging why.
   - Each question's UI should be purpose-built for what that question demonstrates —
     see the "UI notes" section in each question's spec skeleton for specifics
     (e.g. Q1 needs a visible token-by-token stream with tool-call gap indicators;
     Q2 needs upload + 4-section layout; Q3/Q6 need chat-over-document with visible
     citations; Q5 is mostly a small status/proof page, not a full UI).

4. LANGFUSE — real, not decorative.
   - Before building Q2 (the first question that needs it), ask me explicitly:
     Langfuse Cloud or self-hosted? Get an answer before setting anything up.
   - Set up one shared Langfuse client/wrapper (per README's layout), used by every
     question from Q2 onward. Every LLM call gets traced with @observe()-equivalent,
     tagged with meaningful metadata (paper_id, owner_id, question type, etc.).
   - Q5 configures a project-scoped metric/cost alert (Langfuse's Alerts feature,
     under Observability — threshold + time window + notification channel, e.g.
     Slack/webhook). This is distinct from Langfuse's account-level Billing "Spend
     Alerts," which monitor what you owe Langfuse for the platform itself, not your
     LLM provider spend — do not confuse the two, and say explicitly in the Q5 spec
     and writeup which one you configured.

5. SPEC DISCIPLINE ("Openspec specs" per the assignment).
   - Each question's spec must explicitly cover whatever the PDF calls out under
     "What Openspec specs should cover" for that question — the skeletons in
     /docs/specs/ already list these as required sections. Don't leave any blank.
   - For Q6 specifically, the spec must state explicitly, in writing, that access
     control is enforced as a retrieval-time metadata filter on the vector search
     (e.g. WHERE owner_id = current_user), NOT as a system-prompt instruction. This
     is the core grading point of Q6 — do not hand-wave it.

6. WORKING SYSTEM CHECK = DEFINITION OF DONE.
   - Before telling me a question is ready for review, run the PDF's own "Working
     system check" for that question yourself and report the results as part of your
     "ready for review" message. For Q6, this means actually simulating two users
     with two papers and running the cross-user access-control test yourself first,
     showing me the result (refusal, not leak).

7. NO SILENT SCOPE CREEP.
   - Don't add features, libraries, or infra beyond what a question needs. If
     something feels necessary but wasn't specified (e.g. database choice, an auth
     scheme for Q6's owner_id), flag it as a decision point, pick the simplest
     reasonable option, and state your reasoning — don't just silently choose.

===========================================================
START HERE
===========================================================

Confirm you've read the full PDF, the repo scaffolding (specs skeletons, design
tokens, README), and these rules. Then begin ONLY with Question 1: use your
brainstorming skill to turn Q1's "original ask" + "elaboration" into a proper spec,
filling in /docs/specs/q1-spec.md, and show it to me for approval. Then plan it, then
build it, then run the working system check yourself, then stop for my testing.
Do not touch Q2–Q6 yet.
