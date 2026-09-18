import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertIcon, CheckIcon, SparkIcon } from "../components/Icon";
import {
  fetchPaperSpend,
  fetchQ5Config,
  listPapers,
  type PaperSummary,
  type Q5AlertConfig,
  type Q5Evaluation,
} from "../sse";
import proof from "../../../docs/q5-alert-proof-template.md?raw";

const PROOF_PATH = "docs/q5-alert-proof-template.md";

interface Row {
  paper: PaperSummary;
  evaluation: Q5Evaluation | null;
}

function usd(v: number): string {
  return `$${v.toFixed(6)}`;
}

export default function Q5Alert() {
  const [config, setConfig] = useState<Q5AlertConfig | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const cfg = await fetchQ5Config();
        if (!cancelled) setConfig(cfg);
        const papers = await listPapers();
        // only papers that have been asked questions have a spend ledger
        const checks = await Promise.all(
          papers.map(async (p) => {
            try {
              const { evaluation } = await fetchPaperSpend(p.paper_id);
              return { paper: p, evaluation };
            } catch {
              return { paper: p, evaluation: null };
            }
          }),
        );
        if (!cancelled) {
          setRows(
            checks
              .filter((r) => r.evaluation && r.evaluation.turns > 0)
              .sort((a, b) => (b.evaluation?.total_usd ?? 0) - (a.evaluation?.total_usd ?? 0)),
          );
        }
      } catch {
        if (!cancelled) setOffline(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const anyFiring = rows.some((r) => r.evaluation?.alert);

  return (
    <div>
      <div className="topbar-sub">
        <Link className="brand" to="/">
          <SparkIcon size={16} /> Back to Home
        </Link>
      </div>
      <h1>Q5 — Langfuse Alert on Token/Cost</h1>
      <p className="lede q4-lede">
        The tripwire from Q4, wired to real Q3 traffic. The alert itself is a
        project-scoped Langfuse metric alert (Observability → Alerts — not the
        account Billing spend alert); this page shows its exact definition and
        evaluates every paper's ledger against the same threshold. Config path
        and firing evidence: <code>{PROOF_PATH}</code>.
      </p>

      {offline && (
        <div className="notice" role="alert">
          <AlertIcon size={14} />
          <span>Backend offline — start it with ./start.sh to see live ledger data.</span>
        </div>
      )}

      {config && (
        <dl className="q5-config">
          <div>
            <dt>Alert type</dt>
            <dd>Project metric alert (not Billing)</dd>
          </div>
          <div>
            <dt>Metric</dt>
            <dd>totalCost + totalTokens, filtered to Q3 answers</dd>
          </div>
          <div>
            <dt>Time window</dt>
            <dd>{config.window}</dd>
          </div>
          <div>
            <dt>Threshold</dt>
            <dd>
              {config.threshold_tokens.toLocaleString()} tokens or{" "}
              {usd(config.threshold_usd)}
            </dd>
          </div>
          <div>
            <dt>Notification</dt>
            <dd>{config.channel}</dd>
          </div>
          <div>
            <dt>State now</dt>
            <dd>
              <span className={`q5-state ${anyFiring ? "firing" : "ok"}`}>
                {anyFiring ? <AlertIcon size={12} /> : <CheckIcon size={12} />}
                {anyFiring ? "ALERT firing" : "OK — no paper over threshold"}
              </span>
            </dd>
          </div>
        </dl>
      )}

      <h2>Per-paper ledger (Q3 turns, live records)</h2>
      {rows.length === 0 && !offline && (
        <p className="lede q4-lede">
          No paper has Q&amp;A turns yet — ask a question on{" "}
          <Link to="/q3">Q3</Link> and the ledger appears here.
        </p>
      )}
      {rows.length > 0 && config && (
        <table className="q5-table">
          <thead>
            <tr>
              <th>Paper</th>
              <th className="num">Turns</th>
              <th className="num">Tokens</th>
              <th className="num">Cost</th>
              <th className="num">Max turn</th>
              <th>Share of token threshold</th>
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ paper, evaluation }) => {
              const ev = evaluation as Q5Evaluation;
              const pct = Math.min(100, (ev.total_tokens / config.threshold_tokens) * 100);
              return (
                <tr key={paper.paper_id}>
                  <td title={paper.paper_id}>{paper.title || "(untitled)"}</td>
                  <td className="num">{ev.turns}</td>
                  <td className="num">{ev.total_tokens.toLocaleString()}</td>
                  <td className="num">{usd(ev.total_usd)}</td>
                  <td className="num">{ev.max_turn_tokens.toLocaleString()}</td>
                  <td>
                    <span className="q5-bar" aria-hidden="true">
                      <span className={ev.alert ? "over" : ""} style={{ width: `${pct}%` }} />
                    </span>
                  </td>
                  <td>
                    <span className={`q5-state ${ev.alert ? "firing" : "ok"}`}>
                      {ev.alert ? <AlertIcon size={12} /> : <CheckIcon size={12} />}
                      {ev.alert ? "ALERT" : "OK"}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <h2>Proof it fires</h2>
      <div className="md panel">
        <Markdown remarkPlugins={[remarkGfm]}>{proof}</Markdown>
      </div>
    </div>
  );
}