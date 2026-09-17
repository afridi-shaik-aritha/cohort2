"""Q2 — Langfuse wrapper: no-op without keys, real client objects with keys."""
from app.shared import langfuse_client as lc


def test_disabled_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert lc.tracing_enabled() is False
    with lc.analysis_trace(
        paper_id="p_1", title="T", owner_id="demo-user",
        provider="mock", model="mock", metadata={},
    ) as tr:
        assert tr is None
        with lc.observation(name="section:summary", as_type="generation") as obs:
            obs.update(output="hello", usage_details=None)  # must not raise
    lc.flush()


def test_enabled_with_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    assert lc.tracing_enabled() is True


def test_host_prefers_host_env_then_base_url(monkeypatch):
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    assert lc.langfuse_host() == "https://cloud.langfuse.com"
    # regional Cloud deployments configure LANGFUSE_BASE_URL instead
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://jp.cloud.langfuse.com")
    assert lc.langfuse_host() == "https://jp.cloud.langfuse.com"
    monkeypatch.setenv("LANGFUSE_HOST", "https://custom.langfuse.dev")
    assert lc.langfuse_host() == "https://custom.langfuse.dev"
