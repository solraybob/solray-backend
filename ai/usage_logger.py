"""
ai/usage_logger.py — fire-and-forget API usage logging.

Every LLM call records: provider, model, surface, tokens, cost, latency,
success, errors, retries. Writes go through an in-process async queue with
batched DB inserts (every 10 items or 2 seconds, whichever first). The
chat reply is never blocked on a usage insert.

Codex audit note (May 2026): the batched-consumer pattern beats per-request
background tasks under burst load because it reduces DB write amplification
and prevents background-task pile-up.

Failure mode: if the writer falls behind or DB is down, items pile up in
the in-memory queue. The queue is bounded (LOG_QUEUE_MAX) so we drop the
oldest rather than OOM. Drops are counted and surfaced via get_queue_stats().

Feature flag: USAGE_LOG_ENABLED env var. Default true. Set to "0" to disable
writes (graceful degradation when DB is degraded, no redeploy needed).
"""

import asyncio
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

log = logging.getLogger("solray.usage")

# ── Configuration ─────────────────────────────────────────────────────────────

LOG_QUEUE_MAX = int(os.environ.get("USAGE_LOG_QUEUE_MAX", "10000"))
BATCH_SIZE    = int(os.environ.get("USAGE_LOG_BATCH_SIZE", "10"))
BATCH_INTERVAL_S = float(os.environ.get("USAGE_LOG_BATCH_INTERVAL_S", "2.0"))
ENABLED       = os.environ.get("USAGE_LOG_ENABLED", "1") != "0"

# ── Module-level state ────────────────────────────────────────────────────────

_queue: "asyncio.Queue[dict]" = None  # type: ignore
_writer_task: Optional["asyncio.Task"] = None
_stats = {"enqueued": 0, "written": 0, "dropped": 0, "errors": 0}


# ── Public API ────────────────────────────────────────────────────────────────

def log_api_usage(
    *,
    surface: str,
    provider: str,
    model: str,
    user_id: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    duration_ms: Optional[int] = None,
    is_success: bool = True,
    error_type: Optional[str] = None,
    error_message_trunc: Optional[str] = None,
    retries: int = 0,
    is_stream: bool = False,
    request_uuid: Optional[str] = None,
    provider_request_id: Optional[str] = None,
    oracle_prompt_version: Optional[str] = None,
) -> None:
    """Enqueue one usage record. Non-blocking. Safe to call from any context.

    If the writer hasn't been started yet (e.g. called before app startup),
    the record is dropped and counted. This is intentional — usage logging
    is observability, not load-bearing for chat correctness.
    """
    if not ENABLED:
        return
    if _queue is None:
        # Writer not started yet. Drop silently; it will be running before
        # any user-facing call lands in production.
        _stats["dropped"] += 1
        return

    from ai.pricing import compute_cost_usd_micros, PRICING_VERSION

    record = {
        "user_id": user_id,
        "created_at": datetime.utcnow(),
        "surface": surface[:48],
        "provider": provider[:16],
        "model": model[:64],
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cache_creation_tokens": int(cache_creation_tokens or 0),
        "cache_read_tokens": int(cache_read_tokens or 0),
        "total_tokens": int((input_tokens or 0) + (output_tokens or 0)
                            + (cache_creation_tokens or 0) + (cache_read_tokens or 0)),
        "cost_usd_micros": compute_cost_usd_micros(
            model, input_tokens or 0, output_tokens or 0,
            cache_creation_tokens or 0, cache_read_tokens or 0,
        ),
        "pricing_version": PRICING_VERSION,
        "duration_ms": int(duration_ms) if duration_ms is not None else None,
        "is_success": bool(is_success),
        "error_type": error_type[:64] if error_type else None,
        "error_message_trunc": error_message_trunc[:500] if error_message_trunc else None,
        "retries": int(retries or 0),
        "is_stream": bool(is_stream),
        "request_uuid": request_uuid,
        "provider_request_id": provider_request_id[:128] if provider_request_id else None,
        "oracle_prompt_version": oracle_prompt_version[:32] if oracle_prompt_version else None,
    }

    try:
        _queue.put_nowait(record)
        _stats["enqueued"] += 1
    except asyncio.QueueFull:
        _stats["dropped"] += 1
        # Drop oldest to make room for newest. This keeps recent data fresh
        # under sustained burst.
        try:
            _queue.get_nowait()
            _queue.put_nowait(record)
        except Exception:
            pass


def get_queue_stats() -> dict:
    """Returns enqueued/written/dropped/errors plus current queue depth."""
    depth = _queue.qsize() if _queue is not None else 0
    return {**_stats, "queue_depth": depth, "enabled": ENABLED}


# ── Writer task (started on app startup) ──────────────────────────────────────

async def _writer_loop():
    """Drains the queue in batches, writes to DB. Runs forever."""
    from db.database import AsyncSessionLocal, ApiUsage
    log.info("[usage] writer started, batch_size=%d interval=%.1fs", BATCH_SIZE, BATCH_INTERVAL_S)

    pending: list = []
    last_flush = time.monotonic()

    while True:
        timeout = max(0.05, BATCH_INTERVAL_S - (time.monotonic() - last_flush))
        try:
            item = await asyncio.wait_for(_queue.get(), timeout=timeout)
            pending.append(item)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            log.warning("[usage] queue get error: %s", e)
            await asyncio.sleep(0.5)
            continue

        # Flush if batch full or interval elapsed
        if len(pending) >= BATCH_SIZE or (pending and time.monotonic() - last_flush >= BATCH_INTERVAL_S):
            batch = pending
            pending = []
            last_flush = time.monotonic()
            try:
                async with AsyncSessionLocal() as session:
                    rows = [ApiUsage(**rec) for rec in batch]
                    session.add_all(rows)
                    await session.commit()
                _stats["written"] += len(batch)
            except Exception as e:
                _stats["errors"] += len(batch)
                log.warning("[usage] batch write failed (%d rows): %s", len(batch), type(e).__name__)


async def start_writer():
    """Initialize the queue and start the background writer task.

    Idempotent: calling twice is a no-op.
    """
    global _queue, _writer_task
    if _writer_task is not None and not _writer_task.done():
        return
    _queue = asyncio.Queue(maxsize=LOG_QUEUE_MAX)
    _writer_task = asyncio.create_task(_writer_loop(), name="usage-writer")
    log.info("[usage] writer task created (max queue=%d)", LOG_QUEUE_MAX)


async def stop_writer():
    """Flush remaining items and stop the writer. Used on graceful shutdown."""
    global _writer_task
    if _writer_task is None:
        return
    # Give writer a moment to drain
    if _queue is not None:
        drain_deadline = time.monotonic() + 5.0
        while not _queue.empty() and time.monotonic() < drain_deadline:
            await asyncio.sleep(0.1)
    _writer_task.cancel()
    try:
        await _writer_task
    except asyncio.CancelledError:
        pass
    _writer_task = None


# ── Utility: extract usage from provider response objects ─────────────────────

def extract_anthropic_usage(response) -> dict:
    """Returns a dict of token counts from an Anthropic Messages response."""
    u = getattr(response, "usage", None)
    if u is None:
        return {}
    return {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_creation_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }


def extract_openai_usage(response) -> dict:
    """Returns a dict of token counts from an OpenAI ChatCompletion response."""
    u = getattr(response, "usage", None)
    if u is None:
        return {}
    return {
        "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(u, "completion_tokens", 0) or 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    }
