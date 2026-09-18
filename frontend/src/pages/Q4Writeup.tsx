import { Link } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SparkIcon } from "../components/Icon";
// Bundled at build time — /docs is not served by the backend, and fetching it
// at runtime hits the SPA fallback (index.html), which is the HTML blob bug.
import writeup from "../../../docs/q4-writeup-template.md?raw";

const WRITEUP_PATH = "docs/q4-writeup-template.md";

export default function Q4Writeup() {
  return (
    <div>
      <div className="topbar-sub">
        <Link className="brand" to="/">
          <SparkIcon size={16} /> Back to Home
        </Link>
      </div>
      <h1>Q4 — Runaway Token Spend</h1>
      <p className="lede q4-lede">
        Written deliverable: definition, cited source, and a costed example
        grounded in this system's Q2/Q3 implementation. Rendered from{" "}
        <code>{WRITEUP_PATH}</code>.
      </p>
      <div className="md panel">
        <Markdown remarkPlugins={[remarkGfm]}>{writeup}</Markdown>
      </div>
    </div>
  );
}