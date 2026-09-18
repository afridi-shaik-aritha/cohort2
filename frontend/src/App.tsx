import { Link, Route, Routes, useLocation } from "react-router-dom";
import { SparkIcon } from "./components/Icon";
import Home from "./pages/Home";
import Q1Streaming from "./pages/Q1Streaming";
import Q2PaperInference from "./pages/Q2PaperInference";
import Q3RagQa from "./pages/Q3RagQa";
import Q4Writeup from "./pages/Q4Writeup";
import Q5Alert from "./pages/Q5Alert";
import Q6MultiPaperRag from "./pages/Q6MultiPaperRag";

export default function App() {
  const loc = useLocation();
  const isChat = loc.pathname === "/q1";
  return (
    <div className={isChat ? "shell shell-chat" : "shell"}>
      <div className="topbar">
        <Link className="brand" to="/"><SparkIcon size={18} /> PaperPilot — AI Engineer Track</Link>
        <nav>
          <Link to="/">Home</Link>
          <Link to="/q1">Q1</Link>
          <Link to="/q2">Q2</Link>
          <Link to="/q3">Q3</Link>
          <Link to="/q4">Q4</Link>
          <Link to="/q5">Q5</Link>
          <Link to="/q6">Q6</Link>
        </nav>
      </div>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/q1" element={<Q1Streaming />} />
        <Route path="/q2" element={<Q2PaperInference />} />
        <Route path="/q3" element={<Q3RagQa />} />
        <Route path="/q4" element={<Q4Writeup />} />
        <Route path="/q5" element={<Q5Alert />} />
        <Route path="/q6" element={<Q6MultiPaperRag />} />
      </Routes>
    </div>
  );
}
