import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { AlertIcon, ArrowDownIcon, ArrowUpIcon, CheckIcon, RetryIcon, SparkIcon, StopIcon } from "../components/Icon";
import { streamChat, type ChatMsg, type Q1Event } from "../sse";

interface ToolRow {
  id: string; tool: string; label: string;
  args: Record<string, unknown>; done: boolean;
  status?: string; summary?: string; duration_ms?: number;
}
interface Msg {
  role: "user" | "assistant"; text: string; tools: ToolRow[]; note?: string;
  thinking?: boolean; thinkingLabel?: string;
}
const SUGGESTIONS = [
  "What are the Navier–Stokes equations?",
  "What's the weather in Paris?",
  "Calculate 12 * (3 + 4)",
  "What time is it?",
];

const THINKING_PHRASES = [
  "Thinking",
  "Pondering",
  "Reasoning through it",
  "Working it out",
  "Considering the question",
  "Gathering my thoughts",
];

function pickThinking(): string {
  return THINKING_PHRASES[Math.floor(Math.random() * THINKING_PHRASES.length)];
}

export default function Q1Streaming() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [provider, setProvider] = useState("…");
  const [connLost, setConnLost] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const runRef = useRef<string>("");
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // auto-scroll: follow the stream only while the user is pinned to the bottom.
  // If they scroll up mid-generation, we stop yanking them down and show a
  // "jump to latest" pill instead. Pinning resumes when they return to bottom.
  const pinnedRef = useRef(true);
  const streamingRef = useRef(false);
  const [showJump, setShowJump] = useState(false);

  function nearBottom(): boolean {
    const doc = document.documentElement;
    return window.innerHeight + window.scrollY >= doc.scrollHeight - 140;
  }

  useEffect(() => {
    function onScroll() {
      const near = nearBottom();
      pinnedRef.current = near;
      setShowJump(!near && streamingRef.current);
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    streamingRef.current = streaming;
    if (!streaming) setShowJump(false);
  }, [streaming]);

  useEffect(() => {
    if (pinnedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "auto" });
    }
  }, [msgs]);

  useEffect(() => {
    fetch("/api/q1/health").then((r) => r.json()).then((h) =>
      setProvider(h.display ?? h.provider),
    ).catch(() => setProvider("backend offline"));
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  function patchAssistant(fn: (m: Msg) => Msg) {
    setMsgs((prev) => {
      const i = prev.length - 1;
      if (i < 0 || prev[i].role !== "assistant") return prev;
      const next = [...prev];
      next[i] = fn(next[i]);
      return next;
    });
  }

  function handleEvent(e: Q1Event) {
    if (e.type === "run") runRef.current = e.data.run_id;
    else if (e.type === "token") {
      const t = e.data.text;
      patchAssistant((m) => ({ ...m, text: m.text + t, thinking: false }));
    } else if (e.type === "tool_call_start") {
      const row: ToolRow = {
        id: e.data.id, tool: e.data.tool, label: e.data.label,
        args: e.data.args, done: false,
      };
      patchAssistant((m) => ({ ...m, tools: [...m.tools, row] }));
    } else if (e.type === "tool_call_end") {
      const d = e.data;
      patchAssistant((m) => ({
        ...m,
        tools: m.tools.map((t) =>
          t.id === d.id ? { ...t, done: true, status: d.status, summary: d.summary, duration_ms: d.duration_ms } : t,
        ),
      }));
    } else if (e.type === "error") {
      patchAssistant((m) => ({ ...m, note: `Error: ${e.data.message}` }));
    } else if (e.type === "done") {
      if (e.data.stop_reason === "cancelled") patchAssistant((m) => ({ ...m, note: "Cancelled." }));
      setStreaming(false);
    }
  }

  async function send(text: string) {
    const content = text.trim();
    if (!content || streaming) return;
    setConnLost(false);
    const history: ChatMsg[] = [
      ...msgs.map((m) => ({ role: m.role, content: m.text })),
      { role: "user", content },
    ];
    setMsgs((prev) => [...prev,
      { role: "user", text: content, tools: [] },
      { role: "assistant", text: "", tools: [], thinking: true, thinkingLabel: pickThinking() }]);
    setInput("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    setStreaming(true);
    // sending a message implies wanting to watch the answer
    pinnedRef.current = true;
    requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "auto" });
    });
    const ctl = new AbortController();
    abortRef.current = ctl;
    try {
      await streamChat(history, handleEvent, ctl.signal);
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        patchAssistant((m) => ({ ...m, note: "Cancelled." }));
      } else {
        setConnLost(true);
        patchAssistant((m) => ({ ...m, note: "Connection lost — Retry?" }));
      }
    } finally {
      setStreaming(false);
    }
  }

  async function stop() {
    abortRef.current?.abort();
    if (runRef.current) {
      try {
        await fetch(`/api/q1/runs/${runRef.current}/cancel`, { method: "DELETE" });
      } catch { /* UI abort is enough */ }
    }
    setStreaming(false);
  }

  function retry() {
    const lastUser = [...msgs].reverse().find((m) => m.role === "user");
    setMsgs((prev) => prev.slice(0, prev.length - 1));
    if (lastUser) void send(lastUser.text);
  }

  return (
    <div>
      <div className="q1-head">
        <h1>Streaming Chat</h1>
        <span className="provider-badge"><SparkIcon size={12} /> {provider}</span>
      </div>
      {msgs.length === 0 && (
        <div className="empty">
          <h2 className="empty-title">How can I help you today?</h2>
          <div className="suggest">
            {SUGGESTIONS.map((s) => (
              <button key={s} onClick={() => void send(s)}>{s}</button>
            ))}
          </div>
        </div>
      )}
      <div className="chat">
        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div className="msg-user" key={i}>{m.text}</div>
          ) : (
            <div className="msg-assistant" key={i}>
              {m.text && (
                <div className="md">
                  <Markdown
                    remarkPlugins={[remarkGfm, remarkMath]}
                    rehypePlugins={[rehypeKatex]}
                  >
                    {m.text}
                  </Markdown>
                </div>
              )}
              {m.tools.map((t) => (
                <div className={`tool-row${t.done ? " done" : ""}${t.status === "error" ? " error" : ""}`} key={t.id}>
                  {!t.done && <span className="spin" aria-label="working" />}
                  {t.done && (
                    <span className={`tool-icon${t.status === "error" ? " error" : ""}`} aria-hidden="true">
                      {t.status === "error" ? <AlertIcon size={14} /> : <CheckIcon size={14} />}
                    </span>
                  )}
                  <span className="tool-text">{t.done ? (t.summary ?? t.label) : t.label}</span>
                  <details>
                    <summary>details</summary>
                    <span className="mono">{t.tool} {JSON.stringify(t.args).slice(0, 300)}{t.duration_ms != null ? ` · ${t.duration_ms}ms` : ""}</span>
                  </details>
                </div>
              ))}
              {m.note && (
                <div className="notice">
                  {connLost && <RetryIcon size={14} />}
                  <span>{m.note}</span>
                  {connLost && <button className="retry-btn" onClick={retry}>Retry</button>}
                </div>
              )}
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </div>
      <div className="composer">
        {(() => {
          const last = msgs[msgs.length - 1];
          const thinking =
            streaming && last?.role === "assistant" && last.thinking && !last.text && last.tools.length === 0;
          return (
            thinking && (
              <div className="thinking-row" role="status" aria-live="polite">
                <span className="spin" aria-hidden="true" />
                <span className="thinking-label">{last.thinkingLabel ?? "Thinking"}…</span>
              </div>
            )
          );
        })()}
        {showJump && (
          <button
            className="jump-btn"
            onClick={() => {
              pinnedRef.current = true;
              setShowJump(false);
              bottomRef.current?.scrollIntoView({ behavior: "smooth" });
            }}
            aria-label="Jump to latest"
          >
            <ArrowDownIcon size={15} /> Jump to latest
          </button>
        )}
        <div className="composer-box">
          <textarea
            ref={inputRef}
            value={input}
            rows={1}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(input);
              }
            }}
            placeholder="How can I help you today?"
            disabled={streaming}
          />
          <div className="composer-row">
            <span className="composer-model"><SparkIcon size={13} /> {provider}</span>
            {streaming ? (
              <button className="send-btn stop" onClick={() => void stop()} aria-label="Stop generating">
                <StopIcon size={15} />
              </button>
            ) : (
              <button
                className="send-btn"
                onClick={() => void send(input)}
                disabled={!input.trim()}
                aria-label="Send message"
              >
                <ArrowUpIcon size={17} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
