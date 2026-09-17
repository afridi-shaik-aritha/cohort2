import { Link, Route, Routes, useLocation } from "react-router-dom";
import { SparkIcon } from "./components/Icon";
import Home from "./pages/Home";
import Q1Streaming from "./pages/Q1Streaming";
import Q2PaperInference from "./pages/Q2PaperInference";

export default function App() {
  const loc = useLocation();
  const isChat = loc.pathname === "/q1";
  return (
    <div className={isChat ? "shell shell-chat" : "shell"}>
      <div className="topbar">
        <Link className="brand" to="/"><SparkIcon size={18} /> AI Fundamentals → AI Engineer</Link>
        <nav>
          <Link to="/">Home</Link>
          <Link to="/q1">Q1</Link>
          <Link to="/q2">Q2</Link>
        </nav>
      </div>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/q1" element={<Q1Streaming />} />
        <Route path="/q2" element={<Q2PaperInference />} />
      </Routes>
    </div>
  );
}
