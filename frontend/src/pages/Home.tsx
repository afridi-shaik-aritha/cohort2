import { Link } from "react-router-dom";

const QUESTIONS = [
  { n: 1, title: "Streaming Chat UI", desc: "SSE token streaming with tool-call gap indicators.", path: "/q1", status: "Ready to test", cls: "ready", enabled: true },
  { n: 2, title: "Paper Inference Engine", desc: "Upload a paper, get 4 structured sections + Langfuse traces.", path: "", status: "Coming soon", cls: "", enabled: false },
  { n: 3, title: "RAG Q&A over the paper", desc: "Ask questions about the paper with citations.", path: "", status: "Coming soon", cls: "", enabled: false },
  { n: 4, title: "Runaway Token Spend writeup", desc: "Definition, source, and a grounded cost example.", path: "", status: "Coming soon", cls: "", enabled: false },
  { n: 5, title: "Langfuse Alert on Token/Cost", desc: "Project-scoped alert + proof it fires.", path: "", status: "Coming soon", cls: "", enabled: false },
  { n: 6, title: "Multi-paper RAG, access-scoped", desc: "Owner-filtered retrieval with per-paper citations.", path: "", status: "Coming soon", cls: "", enabled: false },
];

export default function Home() {
  return (
    <div>
      <div className="hero">
        <h1>AI Fundamentals → AI Engineer</h1>
        <p>
          One repo, one running system, six questions. Each card routes to that
          question's page. Progress is approved one question at a time.
        </p>
      </div>
      <div className="cards">
        {QUESTIONS.map((q) =>
          q.enabled ? (
            <Link className="card" key={q.n} to={q.path}>
              <h2>Q{q.n} — {q.title}</h2>
              <p>{q.desc}</p>
              <span className={`badge ${q.cls}`}>{q.status}</span>
            </Link>
          ) : (
            <div className="card disabled" key={q.n} aria-disabled="true">
              <h2>Q{q.n} — {q.title}</h2>
              <p>{q.desc}</p>
              <span className={`badge ${q.cls}`}>{q.status}</span>
            </div>
          ),
        )}
      </div>
    </div>
  );
}
