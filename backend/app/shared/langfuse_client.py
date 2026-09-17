"""Langfuse tracing wrapper — real Cloud tracing when keys are set, safe no-op otherwise.

Q2 is the first question that needs observability. Everything here degrades to a
no-op when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are absent, so the whole app
stays runnable and testable without credentials.

SDK facts (langfuse 4.15.4, introspected — v4 differs from v3):
  * trace-level attributes: langfuse.propagate_attributes(trace_name=…, user_id=…,
    session_id=…, tags=[…], metadata={…})
  * observations: client.start_as_current_observation(name=…, as_type="span"|"generation", …)
  * updates: obs.update(output=…, usage_details={"input": n, "output": m, "total": n+m})
  * there is NO start_as_current_span / update_current_trace in v4.
"""
import logging
import os
from contextlib import contextmanager
from typing import Any, Optional

log = logging.getLogger("langfuse_client")
_warned = False


def tracing_enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY")) and bool(os.getenv("LANGFUSE_SECRET_KEY"))


def langfuse_host() -> str:
    """Langfuse host — LANGFUSE_HOST preferred, LANGFUSE_BASE_URL honoured too
    (regional Cloud hosts like https://jp.cloud.langfuse.com use the latter)."""
    return (
        os.getenv("LANGFUSE_HOST")
        or os.getenv("LANGFUSE_BASE_URL")
        or "https://cloud.langfuse.com"
    )


_client_instance: Any = None
_client_config: Optional[tuple] = None


def _client():
    """Returns the shared Langfuse client, or None when tracing is off/unavailable.

    One client per process (SDK-recommended): `propagate_attributes` only
    reaches observations created through the SAME client instance — a fresh
    client per observation fragments one logical trace into many orphan
    traces, which is exactly what the first live run showed.
    """
    global _client_instance, _client_config, _warned
    if not tracing_enabled():
        return None
    config = (
        os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        os.getenv("LANGFUSE_SECRET_KEY", ""),
        langfuse_host(),
    )
    if _client_instance is not None and _client_config == config:
        return _client_instance
    try:
        from langfuse import Langfuse

        _client_instance = Langfuse(
            public_key=config[0],
            secret_key=config[1],
            host=config[2],
        )
        _client_config = config
        return _client_instance
    except Exception as e:  # SDK missing/misconfigured → degrade, never crash a request
        if not _warned:
            log.warning("Langfuse init failed, tracing disabled: %s", e)
            _warned = True
        return None


class _NoOp:
    """Stand-in observation used when tracing is disabled."""

    def update(self, **_kwargs: Any) -> None:
        return None


def _exit_quietly(cm) -> None:
    """Leave an already-entered observation context, containing close errors."""
    if cm is None:
        return
    try:
        cm.__exit__(None, None, None)
    except Exception as e:
        log.warning("Langfuse observation close failed: %s", e)


@contextmanager
def _root_trace(*, trace_name: str, tags: list, paper_id: str, title: str,
                owner_id: str, provider: str, model: str,
                metadata: Optional[dict] = None):
    """Shared shape for analysis_trace / qa_trace.

    Structure matters: SETUP failures are contained (yield None, untraced),
    but exceptions raised by the CALLER's `with` body propagate untouched —
    an earlier version wrapped the body in try/except here and masked a real
    generation bug as a "Langfuse failure" log line. Teardown runs in finally
    on every path.
    """
    client = _client()
    if client is None:
        yield None
        return
    meta = {
        "paper_id": paper_id,
        "title": title,
        "owner_id": owner_id,
        "provider": provider,
        "model": model,
        **(metadata or {}),
    }
    attr_cm = None
    obs_cm = None
    try:
        from langfuse import propagate_attributes

        # SDK contract: propagate_attributes must WRAP the creation of the root
        # span (or sit immediately inside it). Without a root span there is no
        # trace to attach to, and every observation silently becomes its own
        # orphan trace — the failure mode the first live run exposed.
        attr_cm = propagate_attributes(
            trace_name=trace_name,
            user_id=owner_id,
            session_id=paper_id,
            tags=tags,
            metadata=meta,
        )
        attr_cm.__enter__()
        obs_cm = client.start_as_current_observation(
            name=trace_name, as_type="span", metadata=meta
        )
        obs_cm.__enter__()
    except Exception as e:
        log.warning("Langfuse trace setup failed (%s), continuing untraced: %s", trace_name, e)
        _exit_quietly(obs_cm)
        if attr_cm is not None:
            try:
                attr_cm.__exit__(None, None, None)
            except Exception:
                pass
        yield None
        return
    try:
        yield client
    finally:
        _exit_quietly(obs_cm)
        try:
            attr_cm.__exit__(None, None, None)
        except Exception:
            pass


@contextmanager
def analysis_trace(
    *,
    paper_id: str,
    title: str,
    owner_id: str,
    provider: str,
    model: str,
    metadata: Optional[dict] = None,
):
    """One trace per Q2 analyze request. Yields the client, or None when disabled."""
    with _root_trace(
        trace_name="q2.paper_analysis",
        tags=["q2", "paper-analysis", provider],
        paper_id=paper_id, title=title, owner_id=owner_id,
        provider=provider, model=model, metadata=metadata,
    ) as client:
        yield client


@contextmanager
def qa_trace(
    *,
    paper_id: str,
    title: str,
    owner_id: str,
    provider: str,
    model: str,
    metadata: Optional[dict] = None,
):
    """One trace per Q3 Q&A turn. session_id = paper_id so a paper's Q&A turns
    group with its q2.paper_analysis traces in the Langfuse session view."""
    with _root_trace(
        trace_name="q3.paper_qa",
        tags=["q3", "rag-qa", provider],
        paper_id=paper_id, title=title, owner_id=owner_id,
        provider=provider, model=model, metadata=metadata,
    ) as client:
        yield client


@contextmanager
def observation(
    *,
    name: str,
    as_type: str,
    model: Optional[str] = None,
    input: Optional[Any] = None,
    metadata: Optional[dict] = None,
):
    """Span/generation wrapper. Disabled mode yields a no-op recorder.

    Setup failures are contained (no-op); body exceptions propagate — same
    contract as _root_trace.
    """
    client = _client()
    if client is None:
        yield _NoOp()
        return
    kwargs: dict = {"name": name, "as_type": as_type, "input": input, "metadata": metadata}
    if model:
        kwargs["model"] = model
    try:
        cm = client.start_as_current_observation(**kwargs)
        obs = cm.__enter__()
    except Exception as e:
        log.warning("Langfuse observation setup failed (%s): %s", name, e)
        yield _NoOp()
        return
    try:
        yield obs
    finally:
        _exit_quietly(cm)


def flush() -> None:
    client = _client()
    if client is not None:
        try:
            client.flush()
        except Exception:
            pass


def reset_client() -> None:
    """Drop the cached client (tests / key rotation)."""
    global _client_instance, _client_config
    _client_instance = None
    _client_config = None
