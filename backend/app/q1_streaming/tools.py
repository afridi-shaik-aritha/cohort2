"""Q1 demo tools: deterministic, no external APIs, no credentials.

Exposed to real LLMs as function/tool schemas AND to mock mode via
detect_tool_mock() keyword routing so the tool-gap UI is exercisable
with zero API keys.
"""
import ast
import operator
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

TOOL_DEFS = [
    {
        "name": "get_weather",
        "description": "Get current weather for a city (canned demo data). Use ONLY when the user explicitly asks about weather.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    },
    {
        "name": "calculator",
        "description": "Evaluate a basic arithmetic expression. Use ONLY when the user explicitly asks to calculate/compute something.",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "e.g. '12 * (3 + 4)'"}},
            "required": ["expression"],
        },
    },
    {
        "name": "get_current_time",
        "description": "Get the current server time. Use ONLY when the user explicitly asks for the time.",
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string", "description": "IANA label, informational only"}},
        },
    },
]

_WEATHER = {
    "paris": "Paris: 18°C, cloudy",
    "london": "London: 14°C, light rain",
    "new york": "New York: 22°C, sunny",
    "tokyo": "Tokyo: 25°C, humid and clear",
    "san francisco": "San Francisco: 17°C, foggy",
}

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(expr: str) -> float:
    """Evaluate arithmetic only; raises ValueError on anything else."""
    node = ast.parse(expr, mode="eval")

    def _ev(n):
        if isinstance(n, ast.Expression):
            return _ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _SAFE_OPS:
            return _SAFE_OPS[type(n.op)](_ev(n.left), _ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _SAFE_OPS:
            return _SAFE_OPS[type(n.op)](_ev(n.operand))
        raise ValueError("unsupported expression")

    return _ev(node)


def get_weather(city: str) -> str:
    key = (city or "").strip().lower()
    if key in _WEATHER:
        return _WEATHER[key]
    return f"{city.strip()}: 20°C, clear (demo data)"


def calculator(expression: str) -> str:
    try:
        return f"{expression} = {_safe_eval(expression)}"
    except Exception as e:
        raise ValueError(f"could not evaluate {expression!r}: {e}")


def get_current_time(timezone_label: str = "UTC") -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Current time ({timezone_label}): {now}"


TOOL_FUNCS = {
    "get_weather": lambda args: get_weather(args.get("city", "Paris")),
    "calculator": lambda args: calculator(args.get("expression", "")),
    "get_current_time": lambda args: get_current_time(args.get("timezone", "UTC")),
}


TOOL_LABELS = {
    "get_weather": lambda args: f"Checking weather for {args.get('city', '…')}…",
    "calculator": lambda args: f"Calculating {args.get('expression', '…')}…",
    "get_current_time": lambda args: "Getting current time…",
}


def tool_label(name: str, args: dict) -> str:
    fn = TOOL_LABELS.get(name)
    return fn(args) if fn else f"Calling {name}…"


def run_tool(name: str, args: dict) -> tuple[str, str]:
    """Run a tool. Returns (summary, status) with status 'ok'|'error'."""
    fn = TOOL_FUNCS.get(name)
    if not fn:
        return f"Unknown tool: {name}", "error"
    t0 = time.monotonic()
    try:
        result = fn(args or {})
        summary = str(result)[:140]
        return summary, "ok"
    except Exception as e:
        return str(e)[:140], "error"


def detect_tool_mock(text: str) -> Optional[tuple[str, dict]]:
    """Keyword routing for the FIRST turn (mock mode + lmstudio server-routing).

    Returns (tool_name, args) or None. Order matters: explicit
    'calculate <expr>' wins over bare 'what is 2+2'; both avoid matching
    inside code/writing requests because those patterns require the
    message to START with a computation-looking phrase.
    """
    low = text.lower()
    m = re.search(r"weather in ([a-zA-Z][a-zA-Z\s\-']{1,40})", text)
    if m:
        city = m.group(1).strip().rstrip("?.!").title()
        return "get_weather", {"city": city}
    if "weather" in low and low.split()[0] in ("what", "how", "whats", "is", "tell"):
        return "get_weather", {"city": "Paris"}
    m = re.search(r"^(?:please\s+)?calculat\w+\s+([0-9(][0-9+\-*/().\s%^]*)", low)
    if m:
        return "calculator", {"expression": m.group(1).strip().rstrip("?.!")}
    m = re.search(r"^what(?:'s| is)\s+([0-9(][0-9+\-*/().\s%^]*)\s*\??", low)
    if m and any(op in m.group(1) for op in "+-*/%^"):
        return "calculator", {"expression": m.group(1).strip()}
    if re.search(r"^(what(?:'s| is)? ?(?:the )?)?(current )?time", low) or re.search(
        r"^what time", low
    ):
        return "get_current_time", {"timezone": "UTC"}
    if low.startswith(("what time", "current time", "time now", "whats the time")):
        return "get_current_time", {"timezone": "UTC"}
    if re.search(r"\b(fail|broken|crash)\b.*\btool\b", low) or re.search(
        r"\btool\b.*\b(fail|broken|crash)\b", low
    ):
        return "__fail__", {}
    return None
