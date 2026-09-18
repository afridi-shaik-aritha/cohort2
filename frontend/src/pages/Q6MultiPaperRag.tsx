import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { AlertIcon, SparkIcon, StopIcon, UserIcon } from "../components/Icon";
import {
  askQ6,
  cancelQ6Run,
  fetchQ6Health,
  listQ6Papers,
  uploadQ6Paper,
  type PaperMeta,
  type Q6Citation,
  type Q6Event,
  type Q6Health,
  type Q6PaperCited,
  type Q6PaperSummary,
} from "../sse";

const USERS = ["alice", "bob", "demo-user"];

/** Distinct hue per paper so multi-paper answers never visually blend sources. */
const HUES = [26, 152, 210, 262, 330, 45, 96, 285];

function paperHue(paperId: string | undefined): number {
  const s = paperId ?? "";
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return HUES[h % HUES.length];
}

interface Q6Turn {
  question: string;
  answer: string;
  refused: boolean;
  accessDenied: boolean;
  citations: Q6Citation[];
  papersCited: Q6PaperCited[];
  usage: { input: number; output: number; total: number } | null;
  streaming: boolean;
}

const EXAMPLES = [
  "How was the model evaluated?",
  "What is masked language modeling used for?",
  'What does "Attention Is All You Need" say about positional encoding?',
];

export default function Q6MultiPaperRag() {
  const [owner, setOwner] = useState("alice");
  const [papers, setPapers] = useState<Q6PaperSummary[]>([]);
  const [scopeIds, setScopeIds] = useState<string[] | null>(null); // null = all my papers
  const [turns, setTurns] = useState<Q6Turn[]>([]);
  const [input, setInput] = useState("");
  const [phase, setPhase] = useState<"idle" | "asking" | "uploading">("idle");
  const [notice, setNotice] = useState("");
  const [fileName, setFileName] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [health, setHealth] = useState<Q6Health | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runRef = useRef<string>("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const scopeTitles = useMemo(
    () =>
      scopeIds === null
        ? null
        : papers.filter((p) => scopeIds.includes(p.paper_id)).map((p) => p.title),
    [papers, scopeIds],
  );

  useEffect(() => {
    fetchQ6Health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const refresh = useCallback(async (ownerId: string) => {
    try {
      const ps = await listQ6Papers(ownerId);
      setPapers(ps);
      setScopeIds(null); // default: all of this owner's papers
      setTurns([]);
    } catch {
      /* backend offline — the page still renders */
    }
  }, []);

  useEffect(() => {
    void refresh(owner);
  }, [owner, refresh]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const onFile = useCallback(
    async (file: File) => {
      if (phase !== "idle") return;
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setNotice("Only PDF uploads are accepted.");
        return;
      }
      setNotice("");
      setFileName(file.name);
      setPhase("uploading");
      try {
        const meta: PaperMeta = await uploadQ6Paper(file, owner);
        const summary: Q6PaperSummary = {
          paper_id: meta.paper_id,
          title: meta.title,
          authors: meta.authors,
          pages: meta.pages,
          chars: meta.chars,
          created_at: new Date().toISOString(),
          has_sections: false,
        };
        setPapers((prev) => [summary, ...prev]);
        setScopeIds(null);
      } catch (err) {
        const e = err as Error & { detail?: { message?: string; error?: string } };
        setNotice(
          e.detail?.error === "no_extractable_text"
            ? "This PDF has no extractable text — is it a scanned/image-only file?"
            : e.detail?.message ?? e.message ?? "upload failed",
        );
      } finally {
        setPhase("idle");
      }
    },
    [owner, phase],
  );

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || phase !== "idle") return;
      setPhase("asking");
      setNotice("");
      setTurns((prev) => [
        ...prev,
        {
          question: q,
          answer: "",
          refused: false,
          accessDenied: false,
          citations: [],
          papersCited: [],
          usage: null,
          streaming: true,
        },
      ]);
      const ctl = new AbortController();
      abortRef.current = ctl;
      const handle = (e: Q6Event) => {
        setTurns((prev) => {
          const last = prev.length - 1;
          const cur = prev[last];
          if (!cur) return prev;
          const withTurn = (patch: Partial<Q6Turn>): Q6Turn[] =>
            prev.map((t, i) => (i === last ? { ...t, ...patch } : t));
          if (e.type === "run") {
            runRef.current = e.data.run_id;
            return prev;
          }
          if (e.type === "citation") {
            if (cur.citations.some((c) => c.position === e.data.position)) return prev;
            return withTurn({ citations: [...cur.citations, e.data] });
          }
          if (e.type === "token") {
            return withTurn({ answer: cur.answer + e.data.text });
          }
          if (e.type === "done") {
            const refused = e.data.refused ?? false;
            const accessDenied = e.data.access_denied ?? false;
            return withTurn({
              streaming: false,
              refused,
              accessDenied,
              answer: refused ? "" : cur.answer,
              citations: refused ? (e.data.citations ?? cur.citations) : cur.citations,
              papersCited: e.data.papers_cited ?? [],
              usage: e.data.usage,
            });
          }
          if (e.type === "error") {
            setNotice(e.data.message);
            return withTurn({ streaming: false });
          }
          return prev;
        });
      };
      try {
        await askQ6(q, scopeIds, owner, handle, ctl.signal);
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setNotice("connection lost while answering");
        }
      } finally {
        setPhase("idle");
        setTurns((prev) =>
          prev.map((t, i) => (i === prev.length - 1 ? { ...t, streaming: false } : t)),
        );
      }
    },
    [owner, phase, scopeIds],
  );

  function stop() {
    abortRef.current?.abort();
    if (runRef.current) void cancelQ6Run(runRef.current);
    setPhase("idle");
  }

  const busy = phase !== "idle";

  return (
    <div>
      <div className="q1-head">
        <h1>Multi-paper RAG, access-scoped</h1>
        <div className="chip-row">
          <span className="provider-badge">
            <SparkIcon size={12} /> {health ? `${health.provider} · ${health.model}` : "…"}
          </span>
          <span className={`provider-badge obs ${health?.tracing === false ? "obs-off" : ""}`}>
            Observability: {health ? (health.tracing ? "on" : "off") : "…"}
          </span>
        </div>
      </div>

      <div className="q6-ownerbar">
        <span className="q6-ownerbar-label">
          <UserIcon size={13} /> Signed in as
        </span>
        {USERS.map((u) => (
          <button
            key={u}
            className={`q6-owner-chip${u === owner ? " active" : ""}`}
            onClick={() => setOwner(u)}
            disabled={busy}
          >
            {u}
          </button>
        ))}
        <span className="composer-model">
          simulated users via <code>X-Owner-Id</code> — access control is a retrieval-time filter,
          not a prompt rule
        </span>
      </div>

      <div className="paper-picker">
        <div
          className={`q3-dropper${dragOver ? " over" : ""}${busy ? " busy" : ""}`}
          onClick={() => !busy && fileInputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const f = e.dataTransfer.files?.[0];
            if (f) void onFile(f);
          }}
          role="button"
          aria-label={`Upload a paper PDF as ${owner}`}
        >
          {phase === "uploading" ? (
            <>
              <span className="spin spin-lg" aria-hidden="true" />
              <div className="q3-paper-meta">
                <strong>Extracting {fileName}…</strong>
                <span>text → metadata → stored under {owner}</span>
              </div>
            </>
          ) : (
            <div className="q3-paper-meta">
              <strong>Drop a paper PDF to add it to {owner}'s library</strong>
              <span>ingested via the Q2 pipeline — owner stamped at upload</span>
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onFile(f);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      {papers.length > 0 && (
        <div className="q6-scopebar">
          <span className="q6-ownerbar-label">In scope</span>
          <button
            className={`q6-scope-chip${scopeIds === null ? " active" : ""}`}
            onClick={() => setScopeIds(null)}
            disabled={busy}
          >
            All my papers ({papers.length})
          </button>
          {papers.map((p) => (
            <button
              key={p.paper_id}
              className={`q6-scope-chip${scopeIds?.includes(p.paper_id) ? " active" : ""}`}
              onClick={() =>
                setScopeIds(
                  scopeIds === null
                    ? [p.paper_id]
                    : scopeIds.includes(p.paper_id)
                      ? scopeIds.filter((id) => id !== p.paper_id)
                      : [...scopeIds, p.paper_id],
                )
              }
              disabled={busy}
            >
              {p.title || "(untitled)"}
            </button>
          ))}
        </div>
      )}

      {notice && (
        <div className="notice" role="alert">
          <AlertIcon size={14} />
          <span>{notice}</span>
        </div>
      )}

      <div className="chat">
        {turns.length === 0 && papers.length > 0 && (
          <div className="q3-examples">
            <p className="composer-model">
              {scopeTitles && scopeTitles.length > 0
                ? `Asking across: ${scopeTitles.join(", ")}`
                : `Asking across all ${papers.length} of ${owner}'s papers`}
            </p>
            <div className="chip-row">
              {EXAMPLES.map((ex) => (
                <button key={ex} className="ghost-btn" onClick={() => void ask(ex)} disabled={busy}>
                  {ex}
                </button>
              ))}
            </div>
            <p className="composer-model">
              Tip: name another user's paper by title — access is denied before retrieval runs.
            </p>
          </div>
        )}

        {turns.map((t, i) => (
          <div className="q3-turn" key={i}>
            <div className="msg-user">{t.question}</div>
            <div className="msg-assistant">
              {t.accessDenied ? (
                <p className="q3-refusal q6-denial">I don't have access to that paper.</p>
              ) : t.refused ? (
                <p className="q3-refusal">
                  I don't have enough information in the selected paper(s) to answer that.
                </p>
              ) : (
                <div className="md">
                  <Markdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                    {t.answer}
                  </Markdown>
                </div>
              )}
              {t.citations.length > 0 && (
                <div className="q3-citations">
                  {t.citations.map((c) => (
                    <span
                      className="q3-citation q6-citation"
                      key={c.position}
                      title={c.snippet}
                      style={{ ["--paper-hue" as string]: paperHue(c.paper_id) }}
                    >
                      <strong>[{c.position}]</strong> {c.paper_title} —{" "}
                      {c.label.replace(`${c.paper_title} — `, "")}
                    </span>
                  ))}
                </div>
              )}
              {t.papersCited.length > 1 && (
                <p className="composer-model">
                  Sources: {t.papersCited.map((p) => `${p.title} (${p.blocks})`).join(" · ")}
                </p>
              )}
              {t.usage && !t.streaming && (
                <p className="composer-model">
                  {t.usage.total.toLocaleString()} tokens ({t.usage.input.toLocaleString()} in ·{" "}
                  {t.usage.output.toLocaleString()} out)
                </p>
              )}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="composer">
        <div className="composer-box">
          <textarea
            rows={2}
            value={input}
            placeholder={
              papers.length === 0
                ? `Drop a paper PDF above to build ${owner}'s library…`
                : "Ask across your papers — \"this\" resolves to the selector, not a guess…"
            }
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void ask(input);
                setInput("");
              }
            }}
            disabled={phase === "asking"}
          />
          <div className="composer-row">
            <span className="composer-model">
              Retrieval is owner-filtered structurally — another user's paper is never in the
              candidate pool.
            </span>
            {phase === "asking" ? (
              <button className="stop-btn" onClick={stop} aria-label="Stop answering">
                <StopIcon size={14} /> Stop
              </button>
            ) : (
              <button
                className="send-btn"
                onClick={() => {
                  void ask(input);
                  setInput("");
                }}
                disabled={!input.trim()}
              >
                Ask
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
