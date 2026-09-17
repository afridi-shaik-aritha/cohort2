import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { AlertIcon, CheckIcon, SparkIcon, StopIcon } from "../components/Icon";
import {
  askQuestion,
  buildIndex,
  cancelQ3Run,
  fetchIndexStatus,
  fetchQ3Health,
  listPapers,
  uploadPaper,
  type IndexStatus,
  type PaperMeta,
  type PaperSummary,
  type Q3Health,
  type Q3Event,
  type Citation,
} from "../sse";

interface Turn {
  question: string;
  answer: string;
  refused: boolean;
  citations: Citation[];
  usage: { input: number; output: number; total: number } | null;
  streaming: boolean;
}

const EXAMPLES = ["How was this tested?", "What are the references?", "Who are the authors?"];

export default function Q3RagQa() {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [paperId, setPaperId] = useState<string>("");
  const [index, setIndex] = useState<IndexStatus | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [phase, setPhase] = useState<"idle" | "asking" | "uploading" | "indexing">("idle");
  const [notice, setNotice] = useState("");
  const [fileName, setFileName] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [health, setHealth] = useState<Q3Health | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runRef = useRef<string>("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const paper = useMemo(
    () => papers.find((p) => p.paper_id === paperId) ?? null,
    [papers, paperId],
  );

  // Previously uploaded papers, deduped by title (latest wins) — the same PDF
  // re-uploaded through Q2/Q3 must not spawn a list of identical entries.
  const recent = useMemo(() => {
    const byTitle = new Map<string, PaperSummary>();
    for (const p of [...papers].sort((a, b) => b.created_at.localeCompare(a.created_at))) {
      const key = (p.title || "").trim().toLowerCase();
      if (!byTitle.has(key)) byTitle.set(key, p);
    }
    return [...byTitle.values()].slice(0, 6);
  }, [papers]);

  useEffect(() => {
    fetchQ3Health().then(setHealth).catch(() => setHealth(null));
    listPapersSafe();
  }, []);

  async function listPapersSafe() {
    try {
      const ps = await listPapers();
      setPapers(ps);
      if (ps.length > 0) setPaperId((cur) => cur || ps[0].paper_id);
    } catch {
      /* backend offline — the dropper still works once it's back */
    }
  }

  useEffect(() => {
    if (!paperId) {
      setIndex(null);
      setTurns([]);
      return;
    }
    setTurns([]);
    fetchIndexStatus(paperId)
      .then(setIndex)
      .catch(() => setIndex({ indexed: false }));
  }, [paperId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  useEffect(() => () => abortRef.current?.abort(), []);

  /** Any paper works: upload → extract (Q2 pipeline) → index → ready to ask. */
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
        const meta: PaperMeta = await uploadPaper(file);
        const summary: PaperSummary = {
          paper_id: meta.paper_id,
          title: meta.title,
          authors: meta.authors,
          pages: meta.pages,
          chars: meta.chars,
          created_at: new Date().toISOString(),
        };
        setPapers((prev) => [summary, ...prev.filter((p) => p.paper_id !== meta.paper_id)]);
        setPaperId(meta.paper_id); // clears turns, fetches (empty) index status
        setPhase("indexing");
        const s = await buildIndex(meta.paper_id);
        setIndex({
          indexed: true,
          chunks: s.chunks,
          has_references: s.has_references,
        });
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
    [phase],
  );

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || !paperId || phase !== "idle") return;
      setPhase("asking");
      setNotice("");
      setTurns((prev) => [
        ...prev,
        { question: q, answer: "", refused: false, citations: [], usage: null, streaming: true },
      ]);
      const ctl = new AbortController();
      abortRef.current = ctl;
      const handle = (e: Q3Event) => {
        setTurns((prev) => {
          const last = prev.length - 1;
          const cur = prev[last];
          if (!cur) return prev;
          const withTurn = (patch: Partial<Turn>): Turn[] =>
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
            const citations = refused ? (e.data.citations ?? cur.citations) : cur.citations;
            return withTurn({
              streaming: false,
              refused,
              answer: refused ? "" : cur.answer,
              citations,
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
        await askQuestion(paperId, q, handle, ctl.signal);
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
    [paperId, phase],
  );

  function stop() {
    abortRef.current?.abort();
    if (runRef.current) void cancelQ3Run(runRef.current);
    setPhase("idle");
  }

  async function reindex() {
    if (!paperId) return;
    setNotice("");
    try {
      const s = await buildIndex(paperId);
      setIndex({ indexed: true, chunks: s.chunks, has_references: s.has_references });
    } catch (err) {
      setNotice((err as Error).message || "indexing failed");
    }
  }

  const busy = phase !== "idle";
  const indexed = index?.indexed ?? false;

  return (
    <div>
      <div className="q1-head">
        <h1>RAG Q&A over a paper</h1>
        <div className="chip-row">
          <span className="provider-badge">
            <SparkIcon size={12} /> {health ? `${health.provider} · ${health.model}` : "…"}
          </span>
          <span className="provider-badge">
            embed: {health ? health.embedding_model.split("/").pop() : "…"}
          </span>
          <span className={`provider-badge obs ${health?.tracing === false ? "obs-off" : ""}`}>
            Observability: {health ? (health.tracing ? "on" : "off") : "…"}
          </span>
        </div>
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
          aria-label="Upload a paper PDF"
        >
          {phase === "uploading" || phase === "indexing" ? (
            <>
              <span className="spin spin-lg" aria-hidden="true" />
              <div className="q3-paper-meta">
                <strong>
                  {phase === "uploading" ? "Extracting" : "Indexing"} {fileName}…
                </strong>
                <span>text → sections → chunks → embeddings</span>
              </div>
            </>
          ) : paper ? (
            <>
              <CheckIcon size={14} />
              <div className="q3-paper-meta">
                <strong>{paper.title || "(untitled paper)"}</strong>
                <span>
                  {paper.pages} pages · {paper.chars.toLocaleString()} chars · drop another PDF
                  or click to replace
                </span>
              </div>
            </>
          ) : (
            <div className="q3-paper-meta">
              <strong>Drop a paper PDF here, or click to browse</strong>
              <span>any text-based academic paper — extracted, indexed, and ready to ask</span>
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
        <button className="ghost-btn" onClick={() => void reindex()} disabled={!paperId || busy}>
          Rebuild index
        </button>
        <span className={`chip ${indexed ? "chip-done" : "chip-queued"}`}>
          {indexed ? (
            <>
              <CheckIcon size={12} />
              {index?.chunks} chunks
              {index?.has_references ? " · refs" : ""}
            </>
          ) : (
            "not indexed (auto on first ask)"
          )}
        </span>
      </div>

      {recent.length > 0 && (
        <div className="q3-recent">
          <span>Recent:</span>
          {recent.map((p) => (
            <button
              key={p.paper_id}
              className={`q3-recent-chip${p.paper_id === paperId ? " active" : ""}`}
              onClick={() => setPaperId(p.paper_id)}
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
        {turns.length === 0 && paperId && (
          <div className="q3-examples">
            <p className="composer-model">Try one of the assignment's three example questions:</p>
            <div className="chip-row">
              {EXAMPLES.map((ex) => (
                <button key={ex} className="ghost-btn" onClick={() => void ask(ex)} disabled={busy}>
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t, i) => (
          <div className="q3-turn" key={i}>
            <div className="msg-user">{t.question}</div>
            <div className="msg-assistant">
              {t.refused ? (
                <p className="q3-refusal">
                  I don't have enough information in this paper to answer that.
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
                    <span className="q3-citation" key={c.position} title={c.snippet}>
                      <strong>[{c.position}]</strong> {c.label}
                    </span>
                  ))}
                </div>
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
              !paperId
                ? "Drop a paper above to get started…"
                : indexed
                  ? "Ask anything about this paper…"
                  : "First question will index the paper, then answer…"
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
              Answers are grounded in the paper with citations; unanswerable questions are
              refused honestly.
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
                disabled={!input.trim() || !paperId}
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
