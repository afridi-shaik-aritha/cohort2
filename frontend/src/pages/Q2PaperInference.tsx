import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { AlertIcon, CheckIcon, SparkIcon, StopIcon } from "../components/Icon";
import {
  cancelRun,
  streamAnalyze,
  uploadPaper,
  type PaperMeta,
  type Q2Event,
  type SectionUsage,
} from "../sse";

type SectionKey = "technical" | "intuition" | "prerequisites" | "summary";
type PanelStatus = "queued" | "generating" | "done";

const ORDER: SectionKey[] = ["technical", "intuition", "prerequisites", "summary"];

const LABELS: Record<SectionKey, string> = {
  technical: "Technical summary",
  intuition: "Intuition",
  prerequisites: "Prerequisite learning",
  summary: "Summary",
};

interface Panel {
  key: SectionKey;
  text: string;
  status: PanelStatus;
  usage: SectionUsage | null;
  chars: number;
  duration_ms: number;
}

function freshPanels(): Record<SectionKey, Panel> {
  return {
    technical: { key: "technical", text: "", status: "queued", usage: null, chars: 0, duration_ms: 0 },
    intuition: { key: "intuition", text: "", status: "queued", usage: null, chars: 0, duration_ms: 0 },
    prerequisites: { key: "prerequisites", text: "", status: "queued", usage: null, chars: 0, duration_ms: 0 },
    summary: { key: "summary", text: "", status: "queued", usage: null, chars: 0, duration_ms: 0 },
  };
}

function Chip({ status }: { status: PanelStatus }) {
  const cls = { queued: "chip-queued", generating: "chip-generating", done: "chip-done" }[status];
  const label = { queued: "queued", generating: "generating", done: "done" }[status];
  return (
    <span className={`chip ${cls}`}>
      {status === "generating" && <span className="spin" aria-hidden="true" />}
      {status === "done" && <CheckIcon size={12} />}
      {label}
    </span>
  );
}

function usageText(u: SectionUsage | null): string {
  if (!u) return "tokens not reported";
  return `${u.total.toLocaleString()} tokens (${u.input.toLocaleString()} in · ${u.output.toLocaleString()} out)`;
}

export default function Q2PaperInference() {
  const [paper, setPaper] = useState<PaperMeta | null>(null);
  const [panels, setPanels] = useState<Record<SectionKey, Panel>>(freshPanels);
  const [phase, setPhase] = useState<"idle" | "uploading" | "analyzing">("idle");
  const [notice, setNotice] = useState<string>("");
  const [fileName, setFileName] = useState<string>("");
  const [provider, setProvider] = useState("…");
  const [tracing, setTracing] = useState<boolean | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const runRef = useRef<string>("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    fetch("/api/q2/health")
      .then((r) => r.json())
      .then((h: { provider: string; model?: string; tracing: boolean }) => {
        setProvider(`${h.provider} · ${h.model}`);
        setTracing(h.tracing);
      })
      .catch(() => setProvider("backend offline"));
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  const analyze = useCallback(async (paperId: string) => {
    setPhase("analyzing");
    setPanels(freshPanels());
    const ctl = new AbortController();
    abortRef.current = ctl;
    const handleEvent = (e: Q2Event) => {
      if (e.type === "run") {
        runRef.current = e.data.run_id;
      } else if (e.type === "section_start") {
        const key = e.data.key as SectionKey;
        if (!(key in panels)) return;
        setPanels((prev) => ({
          ...prev,
          [key]: { ...prev[key], status: "generating" },
        }));
      } else if (e.type === "token") {
        const key = e.data.section as SectionKey;
        if (!(key in panels)) return;
        setPanels((prev) => ({
          ...prev,
          [key]: { ...prev[key], text: prev[key].text + e.data.text, status: "generating" },
        }));
        setPhase("analyzing");
      } else if (e.type === "section_done") {
        const key = e.data.key as SectionKey;
        if (!(key in panels)) return;
        setPanels((prev) => ({
          ...prev,
          [key]: {
            ...prev[key],
            status: "done",
            usage: e.data.usage,
            chars: e.data.chars,
            duration_ms: e.data.duration_ms,
          },
        }));
      } else if (e.type === "error") {
        setNotice((prev) => (prev ? `${prev} · ${e.data.message}` : e.data.message));
      } else if (e.type === "done") {
        if (e.data.stop_reason === "cancelled") setNotice("Run cancelled — completed sections were kept.");
        setPhase("idle");
      }
    };
    try {
      await streamAnalyze(paperId, handleEvent, ctl.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setNotice((prev) => `${prev ? prev + " · " : ""}connection lost during analysis`);
      }
      setPhase("idle");
    }
  }, []);

  const onFile = useCallback(
    async (file: File) => {
      if (phase === "uploading" || phase === "analyzing") return;
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setNotice("Only PDF uploads are accepted.");
        return;
      }
      setNotice("");
      setFileName(file.name);
      setPhase("uploading");
      try {
        const meta = await uploadPaper(file);
        setPaper(meta);
        await analyze(meta.paper_id);
      } catch (err) {
        const msg =
          (err as { detail?: { message?: string; error?: string } }).detail?.message ??
          (err as Error).message ??
          "upload failed";
        const code = (err as { detail?: { error?: string } }).detail?.error;
        setNotice(
          code === "no_extractable_text"
            ? "This PDF has no extractable text — is it a scanned/image-only file?"
            : msg,
        );
        setPhase("idle");
      }
    },
    [analyze, phase],
  );

  function stop() {
    abortRef.current?.abort();
    if (runRef.current) void cancelRun(runRef.current);
    setPhase("idle");
  }

  function reset() {
    abortRef.current?.abort();
    setPaper(null);
    setPanels(freshPanels());
    setNotice("");
    setFileName("");
    setPhase("idle");
  }

  const busy = phase === "uploading" || phase === "analyzing";
  const totals = ORDER.reduce(
    (acc, k) => {
      const u = panels[k].usage;
      if (u) {
        acc.input += u.input;
        acc.output += u.output;
        acc.total += u.total;
      }
      return acc;
    },
    { input: 0, output: 0, total: 0 },
  );

  return (
    <div>
      <div className="q1-head">
        <h1>Paper Inference Engine</h1>
        <div className="chip-row">
          <span className="provider-badge">
            <SparkIcon size={12} /> {provider}
          </span>
          <span className={`provider-badge obs ${tracing === false ? "obs-off" : ""}`}>
            Observability: {tracing === null ? "…" : tracing ? "on" : "off (add Langfuse keys)"}
          </span>
        </div>
      </div>

      {!paper && (
        <div
          className={`dropzone${dragOver ? " over" : ""}${busy ? " busy" : ""}`}
          onClick={() => fileInputRef.current?.click()}
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
          {phase === "uploading" ? (
            <>
              <span className="spin spin-lg" aria-hidden="true" />
              <p className="dropzone-title">Extracting {fileName || "paper"}…</p>
            </>
          ) : (
            <>
              <p className="dropzone-title">Drop a paper PDF here, or click to choose a file</p>
              <p className="dropzone-sub">
                Text-based academic PDFs work best (≤25MB). Four sections are drafted
                sequentially, each traced in Langfuse.
              </p>
            </>
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
      )}

      {notice && (
        <div className="notice" role="alert">
          <AlertIcon size={14} />
          <span>{notice}</span>
        </div>
      )}

      {paper && (
        <>
          <div className="paper-head">
            <div>
              <h2 className="paper-title">{paper.title || "(untitled paper)"}</h2>
              <p className="paper-meta">
                {paper.authors.length > 0 ? paper.authors.join(", ") : "authors not parsed"}
                {" · "}
                {paper.pages} pages · {paper.chars.toLocaleString()} chars · strategy:{" "}
                {paper.strategy}
              </p>
              {paper.warnings.length > 0 && (
                <p className="paper-warnings">
                  {paper.warnings.map((w) => (
                    <span key={w}>
                      {w}
                      <br />
                    </span>
                  ))}
                </p>
              )}
            </div>
            <div className="paper-actions">
              <button className="ghost-btn" onClick={() => void analyze(paper.paper_id)} disabled={busy}>
                Re-analyze
              </button>
              <button className="ghost-btn" onClick={reset} disabled={busy}>
                New paper
              </button>
            </div>
          </div>

          <div className="q2-grid">
            {ORDER.map((key) => {
              const p = panels[key];
              return (
                <section className="panel" key={key}>
                  <div className="panel-head">
                    <h3>{LABELS[key]}</h3>
                    <Chip status={p.status} />
                  </div>
                  <div className="md panel-body">
                    {p.text ? (
                      <Markdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                        {p.text}
                      </Markdown>
                    ) : (
                      <p className="panel-empty">{p.status === "queued" ? "Waiting…" : "…"}</p>
                    )}
                  </div>
                  {p.status === "done" && (
                    <p className="panel-foot">
                      {p.chars.toLocaleString()} chars · {usageText(p.usage)}
                      {p.duration_ms ? ` · ${(p.duration_ms / 1000).toFixed(1)}s` : ""}
                    </p>
                  )}
                </section>
              );
            })}
          </div>

          <div className="q2-totals">
            {busy ? (
              <button className="stop-btn" onClick={stop} aria-label="Stop generating">
                <StopIcon size={14} /> Stop
              </button>
            ) : (
              <span>
                Run complete — total usage: {totals.total > 0 ? usageText(totals) : "not reported by provider"}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
