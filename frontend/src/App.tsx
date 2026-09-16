import { Link, Route, Routes, useLocation } from "react-router-dom";
import { SparkIcon } from "./components/Icon";
import Home from "./pages/Home";
import Q1Streaming from "./pages/Q1Streaming";

export default function App() {
  return (
    <div className="shell">
      <ShellInner />
    </div>
  );
}

function ShellInner() {
  const loc = useLocation();
  const isChat = loc.pathname === "/q1";
  return (
    <div className={isChat ? "shell-chat-wrap" : undefined}>
      <div className="topbar">
        <Link className="brand" to="/"><SparkIcon size={18} /> AI Fundamentals → AI Engineer</Link>
        <nav>
          <Link to="/">Home</Link>
          <Link to="/q1">Q1</Link>
        </nav>
      </div>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/q1" element={<Q1Streaming />} />
      </Routes>
    </div>
  );
}
