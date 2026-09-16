from app.q1_streaming.tools import calculator, detect_tool_mock, get_weather, run_tool


def test_get_weather_known_and_fallback():
    assert "18" in get_weather("Paris")
    assert "demo data" in get_weather("Atlantis")


def test_calculator_ok_and_bad():
    assert run_tool("calculator", {"expression": "12 * (3 + 4)"})[1] == "ok"
    assert "84" in run_tool("calculator", {"expression": "12 * (3 + 4)"})[0]
    assert run_tool("calculator", {"expression": "__import__('os') cur"})[1] == "error"


def test_unknown_tool_errors():
    summary, status = run_tool("nope", {})
    assert status == "error"


def test_mock_routing():
    assert detect_tool_mock("What's the weather in Paris?")[0] == "get_weather"
    assert detect_tool_mock("calculate 2 + 3")[0] == "calculator"
    assert detect_tool_mock("what time is it?")[0] == "get_current_time"
    assert detect_tool_mock("hello there") is None
    assert calculator("2+3") == "2+3 = 5"
