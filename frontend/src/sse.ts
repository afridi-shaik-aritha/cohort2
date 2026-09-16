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
