"""Ephemeral run_id -> asyncio.Event map with 5-min TTL. No persistence."""
import asyncio
import secrets
import time

_TTL_S = 5 * 60
_runs: dict[str, tuple[asyncio.Event, float]] = {}


def create_run() -> tuple[str, asyncio.Event]:
    run_id = "r_" + secrets.token_hex(4)
    ev = asyncio.Event()
    _runs[run_id] = (ev, time.monotonic())
    _prune()
    return run_id, ev


def get_event(run_id: str):
    item = _runs.get(run_id)
    return item[0] if item else None


def cancel_run(run_id: str) -> bool:
    item = _runs.get(run_id)
    if not item:
        return False
    item[0].set()
    return True


def is_cancelled(run_id: str) -> bool:
    item = _runs.get(run_id)
    return bool(item and item[0].is_set())


def _prune() -> None:
    now = time.monotonic()
    for k in [k for k, (_, ts) in _runs.items() if now - ts > _TTL_S]:
        del _runs[k]
