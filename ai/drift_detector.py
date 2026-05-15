"""
ai/drift_detector.py — Page-Hinkley change-point detection for Oracle audit scores.

Watches the time series of OracleAudit.score and fires an alert when the
running mean drops by more than DELTA points sustained for a window.
This catches silent voice quality decay before user-visible churn.

Page-Hinkley is a classic, simple, low-memory test: maintain running mean,
compute cumulative deviation, alert when deviation exceeds threshold.
Robust to noise; deliberate downturns trip it; one-off bad replies don't.

Codex audit (May 2026) said: start with one metric, add others when there
is signal. So we start with audit_score and a single window.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

log = logging.getLogger("solray.drift")

# Defaults. Tunable via env or future config.
DELTA = 2.0      # ignore deviations smaller than this (acts as noise floor)
LAMBDA = 30.0   # alert threshold on cumulative mean-minus-x; higher = more conservative
WINDOW_DAYS = 7  # rolling window for the running mean


def page_hinkley(values: List[float], delta: float = DELTA, threshold: float = LAMBDA) -> Tuple[bool, float, float, int]:
    """Run Page-Hinkley over a series of scores.

    Returns (alert_fired, page_hinkley_statistic, mean_at_alert, change_point_index).
    If no alert fires, returns (False, max_stat, overall_mean, -1).
    """
    n = len(values)
    if n < 8:
        return False, 0.0, sum(values) / n if n else 0.0, -1

    running_mean = values[0]
    cumulative = 0.0
    max_stat = 0.0
    change_idx = -1

    for i, x in enumerate(values):
        # Update running mean
        running_mean = ((running_mean * i) + x) / (i + 1)
        # Cumulative deviation: drop in score relative to running mean exceeds delta
        cumulative = cumulative + (running_mean - x - delta)
        if cumulative < 0:
            cumulative = 0.0
        if cumulative > max_stat:
            max_stat = cumulative
            change_idx = i
        if cumulative > threshold:
            return True, cumulative, running_mean, i

    return False, max_stat, running_mean, change_idx


async def detect_audit_drift(db, surface: str = "chat", window_days: int = WINDOW_DAYS) -> dict:
    """Run drift detection over the last N days of OracleAudit scores.

    Writes an AuditDriftAlert row if an alert fires. Returns a dict with
    the detection result for the caller (typically the admin endpoint).
    """
    from sqlalchemy import select
    from db.database import OracleAudit, AuditDriftAlert

    since = datetime.utcnow() - timedelta(days=window_days)
    q = await db.execute(
        select(OracleAudit.score, OracleAudit.created_at)
        .where(OracleAudit.created_at >= since)
        .order_by(OracleAudit.created_at.asc())
    )
    rows = q.fetchall()
    scores = [float(r.score) for r in rows]

    if len(scores) < 8:
        return {
            "alert_fired": False,
            "samples": len(scores),
            "note": "insufficient samples (need >= 8)",
        }

    fired, stat, mean_at, idx = page_hinkley(scores)

    if fired:
        # Persist the alert
        alert = AuditDriftAlert(
            surface=surface,
            metric="audit_score_page_hinkley",
            window_days=window_days,
            value=float(stat),
            threshold=LAMBDA,
            samples=len(scores),
            notes=f"running_mean={mean_at:.2f} at change_index={idx}",
        )
        db.add(alert)
        await db.commit()
        log.warning(
            "[drift] ALERT fired surface=%s samples=%d stat=%.2f mean=%.2f",
            surface, len(scores), stat, mean_at,
        )

    return {
        "alert_fired": fired,
        "samples": len(scores),
        "statistic": stat,
        "threshold": LAMBDA,
        "running_mean": mean_at,
        "change_index": idx,
        "first_score_at": rows[0].created_at.isoformat() + "Z" if rows else None,
        "last_score_at": rows[-1].created_at.isoformat() + "Z" if rows else None,
    }
