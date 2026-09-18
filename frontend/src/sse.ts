/** Minimal fetch+ReadableStream SSE client for the Q1 event protocol. */

export type Q1Event =
  | { type: "run"; data: { run_id: string } }
  | { type: "token"; data: { text: string } }
  | { type: "tool_call_start"; data: { id: string; tool: string; label: string; args: Record<string, unknown> } }
  | { type: "tool_call_end"; data: { id: string; tool: string; status: string; summary: string; duration_ms: number } }
  | { type: "error"; data: { message: string; recoverable: boolean } }
  | { type: "done"; data: { stop_reason: string; usage: unknown } };

export interface ChatMsg {
  role: string;
  content: string;
}

export async function streamChat(
  messages: ChatMsg[],
  onEvent: (e: Q1Event) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/q1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of frame.split("\n")) {
        const t = line.trim();
        if (!t || t.startsWith(":")) continue;
        if (t.startsWith("data:")) {
          try {
            onEvent(JSON.parse(t.slice(5).trim()) as Q1Event);
          } catch {
            /* ignore malformed frame */
          }
        }
      }
    }
  }
}

/** ── Q2 additions: paper upload + four-section analysis SSE ─────────────── */

export interface PaperMeta {
  paper_id: string;
  title: string;
  authors: string[];
  pages: number;
  chars: number;
  strategy: string;
  warnings: string[];
}

export interface SectionUsage {
  input: number;
  output: number;
  total: number;
}

export type Q2Event =
  | { type: "run"; data: { run_id: string } }
  | { type: "section_start"; data: { key: string; label: string; index: number; total: number } }
  | { type: "token"; data: { section: string; text: string } }
  | { type: "section_done"; data: { key: string; chars: number; usage: SectionUsage | null; duration_ms: number } }
  | { type: "error"; data: { message: string; recoverable: boolean } }
  | { type: "done"; data: { stop_reason: string; usage: SectionUsage | null } };

interface HttpError extends Error {
  detail?: Record<string, unknown>;
}

async function errorFromResponse(res: Response): Promise<HttpError> {
  let detail: Record<string, unknown> = {};
  try {
    detail = (await res.json()) as Record<string, unknown>;
  } catch {
    /* non-JSON body */
  }
  const d = (detail.detail ?? {}) as Record<string, unknown>;
  const err = new Error(
    (d.message as string) || (detail.message as string) || `request failed: ${res.status}`,
  ) as HttpError;
  err.detail = d;
  return err;
}

/** Parse one SSE HTTP response, invoking onEvent per JSON data frame. */
async function parseFrames(res: Response, onEvent: (e: Q2Event) => void): Promise<void> {
  if (!res.ok || !res.body) throw await errorFromResponse(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of frame.split("\n")) {
        const t = line.trim();
        if (!t || t.startsWith(":")) continue;
        if (t.startsWith("data:")) {
          try {
            onEvent(JSON.parse(t.slice(5).trim()) as Q2Event);
          } catch {
            /* ignore malformed frame */
          }
        }
      }
    }
  }
}

export async function uploadPaper(file: File): Promise<PaperMeta> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/q2/papers", { method: "POST", body: form });
  if (!res.ok) throw await errorFromResponse(res);
  return (await res.json()) as PaperMeta;
}

export async function streamAnalyze(
  paperId: string,
  onEvent: (e: Q2Event) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/q2/papers/${paperId}/analyze`, {
    method: "POST",
    signal,
  });
  await parseFrames(res, onEvent);
}

export async function cancelRun(runId: string): Promise<void> {
  try {
    await fetch(`/api/q2/runs/${runId}/cancel`, { method: "DELETE" });
  } catch {
    /* UI abort is enough */
  }
}

/** ── Q3 additions: RAG Q&A over a paper ─────────────────────────────────── */

export interface Citation {
  position: number;
  label: string;
  snippet: string;
}

export interface TurnUsage {
  input: number;
  output: number;
  total: number;
}

export type Q3Event =
  | { type: "run"; data: { run_id: string } }
  | { type: "citation"; data: Citation }
  | { type: "token"; data: { text: string } }
  | { type: "error"; data: { message: string; recoverable: boolean } }
  | {
      type: "done";
      data: {
        stop_reason: string;
        usage: TurnUsage | null;
        refused?: boolean;
        citations?: Citation[];
      };
    };

export async function askQuestion(
  paperId: string,
  question: string,
  onEvent: (e: Q3Event) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/q3/papers/${paperId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!res.ok || !res.body) throw await errorFromResponse(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of frame.split("\n")) {
        const t = line.trim();
        if (!t || t.startsWith(":")) continue;
        if (t.startsWith("data:")) {
          try {
            onEvent(JSON.parse(t.slice(5).trim()) as Q3Event);
          } catch {
            /* ignore malformed frame */
          }
        }
      }
    }
  }
}

export interface IndexStatus {
  indexed: boolean;
  chunks?: number;
  has_references?: boolean;
  references_chars?: number;
  embedding_model?: string;
  built_at?: string;
  qa_turns?: number;
}

export async function fetchIndexStatus(paperId: string): Promise<IndexStatus> {
  const res = await fetch(`/api/q3/papers/${paperId}/index`);
  if (!res.ok) throw await errorFromResponse(res);
  return (await res.json()) as IndexStatus;
}

export async function buildIndex(paperId: string): Promise<{ chunks: number; has_references: boolean }> {
  const res = await fetch(`/api/q3/papers/${paperId}/index`, { method: "POST" });
  if (!res.ok) throw await errorFromResponse(res);
  return (await res.json()) as { chunks: number; has_references: boolean };
}

export interface Q3Health {
  provider: string;
  model: string;
  embedding_provider: string;
  embedding_model: string;
  tracing: boolean;
  langfuse_host: string;
}

export async function fetchQ3Health(): Promise<Q3Health> {
  const res = await fetch("/api/q3/health");
  if (!res.ok) throw await errorFromResponse(res);
  return (await res.json()) as Q3Health;
}

export interface PaperSummary {
  paper_id: string;
  title: string;
  authors: string[];
  pages: number;
  chars: number;
  created_at: string;
}

export async function listPapers(): Promise<PaperSummary[]> {
  const res = await fetch("/api/q2/papers");
  if (!res.ok) throw await errorFromResponse(res);
  const body = (await res.json()) as { papers: PaperSummary[] };
  return body.papers ?? [];
}

export async function cancelQ3Run(runId: string): Promise<void> {
  try {
    await fetch(`/api/q3/runs/${runId}/cancel`, { method: "DELETE" });
  } catch {
    /* UI abort is enough */
  }
}

/** ── Q5 additions: runaway-spend alert config + per-paper ledger ─────────── */

export interface Q5AlertConfig {
  alert_type: string;
  metric: string;
  companion_metric: string;
  window: string;
  threshold_usd: number;
  threshold_tokens: number;
  single_turn_tokens: number;
  channel: string;
  pricing_basis: { price_in_per_token: number; price_out_per_token: number };
}

export interface Q5Evaluation {
  paper_id: string;
  turns: number;
  window: number;
  total_tokens: number;
  total_usd: number;
  max_turn_tokens: number;
  thresholds: {
    healthy_turn_max_tokens: number;
    runaway_session_tokens: number;
    runaway_session_usd: number;
  };
  trips: { tokens: boolean; cost: boolean };
  alert: boolean;
}

export async function fetchQ5Config(): Promise<Q5AlertConfig> {
  const res = await fetch("/api/q5/config");
  if (!res.ok) throw await errorFromResponse(res);
  return (await res.json()) as Q5AlertConfig;
}

export async function fetchPaperSpend(
  paperId: string,
): Promise<{ config: Q5AlertConfig; evaluation: Q5Evaluation }> {
  const res = await fetch(`/api/q5/papers/${paperId}/spend`);
  if (!res.ok) throw await errorFromResponse(res);
  return (await res.json()) as { config: Q5AlertConfig; evaluation: Q5Evaluation };
}
