"""hive.py — Solray Hive Mind engine

Implements Phases 1-5 of HIVE_MIND_ARCHITECTURE.md (designed by Opus). Each
function is idempotent and safe to call repeatedly. The architecture's SQL
expressions are translated to engine-agnostic SQLAlchemy where it serves
clarity, dropped to raw SQL where the doc's expression is cleaner.

Phases live here:
  Phase 1: discover_cohorts(), rebuild_correlations()
  Phase 2: get_user_collective_context() (consumed by ai/chat.py)
  Phase 3: compute_user_resonance(), compute_resonance_for_all()
  Phase 4: emerge_themes_from_memories() (called by memory synthesis)
  Phase 5: write_daily_hive_metrics()

Privacy invariants (enforced everywhere):
  - k-anonymity threshold k_min = 10 minimum for any cohort to exist
  - confidence buckets: 10-24 -> 0.6, 25-99 -> 0.8, 100+ -> 1.0
  - Oracle only cites patterns with confidence >= 0.8 directly. Patterns
    at 0.6 are surfaced as "emerging" with explicit qualifiers.
  - Differential-privacy noise on surfaced counts (Laplace, scale=2).
  - Pattern tables exclude users where hive_consent=False (rebuild jobs
    walk users.hive_consent and skip).
"""

from __future__ import annotations

import json
import logging
import math
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import (
    ChartComponent,
    ChartSignal,
    HiveMetric,
    PatternCohort,
    PatternCorrelation,
    PatternTheme,
    User,
    UserMemory,
    UserResonance,
)

log = logging.getLogger("solray.hive")

# Privacy thresholds
K_MIN = 10                    # below this, no cohort exists
CONF_EMERGING = 0.6           # k=10..24
CONF_STRONG = 0.8             # k=25..99
CONF_CANONICAL = 1.0          # k=100+

# Correlation cutoffs
CORR_MIN_TOGETHER = 5         # min co-occurrence for correlation row
CORR_STRONG = 0.7             # surface threshold for Oracle prompt

# Differential privacy: Laplace noise scale
LAPLACE_SCALE = 2.0


def _confidence_for(member_count: int) -> float:
    """Map cohort size to confidence per architecture section 2."""
    if member_count >= 100:
        return CONF_CANONICAL
    if member_count >= 25:
        return CONF_STRONG
    if member_count >= K_MIN:
        return CONF_EMERGING
    return 0.0


def _laplace(scale: float = LAPLACE_SCALE) -> float:
    """Draw a Laplace(0, scale) sample. Used for differential privacy on
    surfaced counts. Noisy enough that exact cohort sizes can't be inferred,
    quiet enough that the qualitative picture survives.
    """
    u = random.random() - 0.5
    return -scale * math.copysign(1.0, u) * math.log(1 - 2 * abs(u) + 1e-12)


# ---------------------------------------------------------------------------
# Phase 1: Cohort Discovery
# ---------------------------------------------------------------------------

async def discover_cohorts(db: AsyncSession, max_combo_size: int = 3) -> dict:
    """Walk every consenting user's chart components and build cohort rows.

    A cohort is a set of (component_type=value) filters with k>=K_MIN users.
    Two-pass build:
      1. Single-component cohorts (sun_sign=Aries, hd_type=Manifestor, etc.)
      2. Multi-component cohorts up to size `max_combo_size` (sun+hd_type, etc.)

    Multi-component combinations are pruned aggressively: we only consider
    combinations where each individual component already meets k>=K_MIN.
    Without that prune, the search space is exponential.

    Returns {created, updated, removed, total} for observability.
    """
    # Pull all user_ids that consent to hive participation.
    consenting = (await db.execute(
        select(User.id).where(User.hive_consent == True)  # noqa: E712
    )).scalars().all()
    consenting_set = set(consenting)

    if not consenting_set:
        log.info("[hive] discover_cohorts: 0 consenting users, nothing to do")
        return {"created": 0, "updated": 0, "removed": 0, "total": 0}

    # Pull every (signal_id, component_type, component_value) joined with
    # the signal's user_id, filtering to consenting users.
    rows = (await db.execute(
        select(
            ChartSignal.user_id,
            ChartComponent.component_type,
            ChartComponent.component_value,
        )
        .join(ChartSignal, ChartSignal.signal_id == ChartComponent.signal_id)
    )).all()

    # user_id -> set of (component_type, component_value)
    user_components: dict[str, set] = defaultdict(set)
    for uid, ctype, cval in rows:
        if uid not in consenting_set:
            continue
        user_components[uid].add((ctype, cval))

    # Single-component cohorts: count distinct users per component pair
    single_counts: Counter = Counter()
    for uid, comps in user_components.items():
        for c in comps:
            single_counts[c] += 1

    # Keep only components that hit K_MIN themselves
    eligible_singles = {c for c, n in single_counts.items() if n >= K_MIN}

    # Multi-component cohorts: count combos of size 2..max_combo_size where
    # every member is an eligible single. This is the expensive path.
    multi_counts: Counter = Counter()
    if max_combo_size >= 2:
        from itertools import combinations
        for uid, comps in user_components.items():
            eligible_for_user = sorted(comps & eligible_singles)
            for size in range(2, min(max_combo_size, len(eligible_for_user)) + 1):
                for combo in combinations(eligible_for_user, size):
                    multi_counts[combo] += 1

    # Build the desired set of cohort rows
    desired: dict[str, tuple[list, int]] = {}  # cohort_name -> (filters, count)
    for c, n in single_counts.items():
        if n < K_MIN:
            continue
        ctype, cval = c
        name = f"{ctype}={cval}"
        desired[name] = ([{"type": ctype, "value": cval}], n)
    for combo, n in multi_counts.items():
        if n < K_MIN:
            continue
        name = "_".join(f"{ct}={cv}" for ct, cv in combo)
        desired[name] = ([{"type": ct, "value": cv} for ct, cv in combo], n)

    # Sync to pattern_cohorts: upsert by cohort_name, remove cohorts no
    # longer in the desired set (they fell below k_min).
    existing = (await db.execute(select(PatternCohort))).scalars().all()
    existing_by_name = {c.cohort_name: c for c in existing}

    created = 0
    updated = 0
    for name, (filters, n) in desired.items():
        conf = _confidence_for(n)
        hit = existing_by_name.get(name)
        if hit:
            hit.member_count = n
            hit.confidence_score = conf
            hit.cohort_definition = json.dumps({"filters": filters})
            hit.last_updated = datetime.utcnow()
            updated += 1
        else:
            db.add(PatternCohort(
                cohort_name=name,
                cohort_definition=json.dumps({"filters": filters}),
                member_count=n,
                confidence_score=conf,
            ))
            created += 1

    # Remove cohorts that fell below k_min — privacy invariant: nothing
    # below k=10 may persist as a discoverable cohort.
    removed = 0
    desired_names = set(desired.keys())
    for name, row in existing_by_name.items():
        if name not in desired_names:
            await db.delete(row)
            removed += 1

    await db.commit()

    out = {
        "created": created,
        "updated": updated,
        "removed": removed,
        "total": len(desired_names),
    }
    log.info(f"[hive] discover_cohorts: {out}")
    return out


# ---------------------------------------------------------------------------
# Phase 1: Correlation Engine
# ---------------------------------------------------------------------------

async def rebuild_correlations(db: AsyncSession) -> dict:
    """Compute pairwise component correlations across the user base.

    Phi coefficient as the strength metric: phi = (n_together * total_n) /
    (n_a * n_b). Values >1 mean positive correlation (more co-occurrence
    than independence would predict). The architecture doc filters
    co-occurrence >= CORR_MIN_TOGETHER (5) which is below k_min and that's
    fine because correlations are component-pair statistics, not user-set
    cohorts; the actual k-anonymity constraint kicks in at the cohort
    layer that gets surfaced to users.
    """
    consenting = (await db.execute(
        select(User.id).where(User.hive_consent == True)  # noqa: E712
    )).scalars().all()
    consenting_set = set(consenting)

    if not consenting_set:
        log.info("[hive] rebuild_correlations: no consenting users")
        return {"updated": 0, "total_pairs": 0}

    rows = (await db.execute(
        select(
            ChartSignal.user_id,
            ChartComponent.component_type,
            ChartComponent.component_value,
        )
        .join(ChartSignal, ChartSignal.signal_id == ChartComponent.signal_id)
    )).all()

    user_components: dict[str, set] = defaultdict(set)
    for uid, ctype, cval in rows:
        if uid in consenting_set:
            user_components[uid].add(f"{ctype}={cval}")

    total_n = len(user_components)
    if total_n == 0:
        return {"updated": 0, "total_pairs": 0}

    # n_a per component
    n_per: Counter = Counter()
    for comps in user_components.values():
        for c in comps:
            n_per[c] += 1

    # Co-occurrence pairs
    pair_counts: Counter = Counter()
    for comps in user_components.values():
        sorted_c = sorted(comps)
        for i in range(len(sorted_c)):
            for j in range(i + 1, len(sorted_c)):
                a, b = sorted_c[i], sorted_c[j]
                # Cross-type only: phi between sun_sign=Aries and sun_sign=Taurus
                # is meaningless (mutually exclusive). Skip same-type pairs.
                a_type = a.split('=', 1)[0]
                b_type = b.split('=', 1)[0]
                if a_type == b_type:
                    continue
                pair_counts[(a, b)] += 1

    # Wipe existing and rebuild — simpler than UPSERT ON CONFLICT and the
    # table is small enough.
    await db.execute(sql_delete(PatternCorrelation))

    inserted = 0
    for (a, b), n_together in pair_counts.items():
        if n_together < CORR_MIN_TOGETHER:
            continue
        n_a = n_per[a]
        n_b = n_per[b]
        if n_a == 0 or n_b == 0:
            continue
        # Phi-style strength: n_together * total / (n_a * n_b)
        # 1.0 = independence, >1.0 = positive, <1.0 = negative
        strength = (n_together * total_n) / (n_a * n_b)
        if strength <= 1.0:
            continue
        db.add(PatternCorrelation(
            component_a=a,
            component_b=b,
            co_occurrence_count=n_together,
            total_sample_n=total_n,
            correlation_strength=float(strength),
        ))
        inserted += 1

    await db.commit()
    log.info(f"[hive] rebuild_correlations: inserted={inserted} total_users={total_n}")
    return {"updated": inserted, "total_pairs": len(pair_counts)}


# ---------------------------------------------------------------------------
# Phase 2: RAG retrieval — what does the collective know about THIS user?
# ---------------------------------------------------------------------------

async def get_user_collective_context(
    db: AsyncSession,
    user_id: str,
    confidence_min: float = CONF_STRONG,
    theme_min: float = 0.5,
    correlation_min: float = CORR_STRONG,
    max_themes: int = 6,
    max_correlations: int = 6,
) -> dict:
    """Build the COLLECTIVE RESONANCE CONTEXT block for one user, per message.

    Returns a dict that ai/chat.py renders into the prompt. Empty dict when
    the user has no hive consent or when nothing in the collective passes
    the surface thresholds.
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.hive_consent:
        return {}

    # Pull this user's components
    rows = (await db.execute(
        select(ChartComponent.component_type, ChartComponent.component_value)
        .join(ChartSignal, ChartSignal.signal_id == ChartComponent.signal_id)
        .where(ChartSignal.user_id == user_id)
    )).all()
    user_comps = {f"{t}={v}" for t, v in rows}
    if not user_comps:
        return {}

    # Match to cohorts: any cohort whose filters are a SUBSET of user_comps
    cohorts = (await db.execute(select(PatternCohort))).scalars().all()
    matched: list[PatternCohort] = []
    for c in cohorts:
        try:
            spec = json.loads(c.cohort_definition or '{}')
            filters = spec.get('filters', [])
            wanted = {f"{f['type']}={f['value']}" for f in filters}
            if wanted and wanted.issubset(user_comps):
                matched.append(c)
        except Exception:
            continue

    if not matched:
        return {}

    # Sort cohorts by (confidence desc, member_count desc)
    matched.sort(key=lambda c: (c.confidence_score, c.member_count), reverse=True)

    # Pull themes for the top matched cohorts
    canonical = []   # confidence >= CONF_STRONG
    emerging = []    # CONF_EMERGING <= confidence < CONF_STRONG
    for cohort in matched[:8]:  # limit cohort scan
        themes = (await db.execute(
            select(PatternTheme)
            .where(
                PatternTheme.cohort_id == cohort.cohort_id,
                PatternTheme.emergence_confidence >= theme_min,
            )
            .order_by(PatternTheme.emergence_confidence.desc(), PatternTheme.emergence_count.desc())
            .limit(3)
        )).scalars().all()
        for t in themes:
            noisy_count = max(K_MIN, int(round(cohort.member_count + _laplace())))
            entry = {
                "cohort_name": cohort.cohort_name,
                "member_count": noisy_count,
                "theme_type": t.theme_type,
                "theme_content": t.theme_content,
                "emergence_confidence": float(t.emergence_confidence),
            }
            if cohort.confidence_score >= confidence_min:
                canonical.append(entry)
            else:
                emerging.append(entry)

    canonical = canonical[:max_themes]
    emerging = emerging[:max_themes]

    # Correlations involving any of this user's components
    corrs = (await db.execute(
        select(PatternCorrelation)
        .where(PatternCorrelation.correlation_strength >= correlation_min)
        .order_by(PatternCorrelation.correlation_strength.desc())
        .limit(40)  # filter further below
    )).scalars().all()
    corr_out = []
    for c in corrs:
        if c.component_a in user_comps or c.component_b in user_comps:
            corr_out.append({
                "component_a": c.component_a,
                "component_b": c.component_b,
                "strength": float(c.correlation_strength),
                "co_occurrence_count": c.co_occurrence_count,
            })
            if len(corr_out) >= max_correlations:
                break

    return {
        "canonical": canonical,
        "emerging": emerging,
        "correlations": corr_out,
        "matched_cohort_count": len(matched),
    }


# ---------------------------------------------------------------------------
# Phase 3: Resonance Score
# ---------------------------------------------------------------------------

async def compute_user_resonance(db: AsyncSession, user_id: str) -> Optional[UserResonance]:
    """Compute and persist the per-user resonance score per architecture sec 4.

    Resonance =
      0.4 * Cohort_Connectedness (min(cohort_count / 15, 1.0))
      0.3 * Pattern_Richness     (pattern_diversity / 50)
      0.2 * Emergence_Velocity   (min(new_themes_per_week / 2, 1.0))
      0.1 * Uniqueness_Value     ((50 - avg_cohort_size) / 50)

    Common charts score high on connection + richness. Rare charts score
    high on uniqueness + velocity. Both are held; neither is "better."
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.hive_consent:
        return None

    # User's components
    rows = (await db.execute(
        select(ChartComponent.component_type, ChartComponent.component_value)
        .join(ChartSignal, ChartSignal.signal_id == ChartComponent.signal_id)
        .where(ChartSignal.user_id == user_id)
    )).all()
    user_comps = {f"{t}={v}" for t, v in rows}

    if not user_comps:
        return None

    cohorts = (await db.execute(select(PatternCohort))).scalars().all()
    matched: list[PatternCohort] = []
    for c in cohorts:
        try:
            spec = json.loads(c.cohort_definition or '{}')
            filters = spec.get('filters', [])
            wanted = {f"{f['type']}={f['value']}" for f in filters}
            if wanted and wanted.issubset(user_comps):
                matched.append(c)
        except Exception:
            continue

    cohort_count = len(matched)
    avg_cohort_size = (sum(c.member_count for c in matched) // cohort_count) if cohort_count else 0

    # Pattern diversity: distinct themes visible to them across matched cohorts
    matched_ids = [c.cohort_id for c in matched]
    diversity = 0
    if matched_ids:
        diversity_row = (await db.execute(
            select(func.count(PatternTheme.theme_id))
            .where(PatternTheme.cohort_id.in_(matched_ids))
            .where(PatternTheme.emergence_confidence >= 0.5)
        )).scalar() or 0
        diversity = int(diversity_row)

    # Emergence velocity: new themes in their cohorts per week (last 7d)
    emergence_velocity = 0.0
    if matched_ids:
        week_ago = datetime.utcnow() - timedelta(days=7)
        new_themes = (await db.execute(
            select(func.count(PatternTheme.theme_id))
            .where(PatternTheme.cohort_id.in_(matched_ids))
            .where(PatternTheme.first_observed >= week_ago)
        )).scalar() or 0
        emergence_velocity = float(new_themes)

    cohort_connectedness = min(cohort_count / 15.0, 1.0)
    pattern_richness = min(diversity / 50.0, 1.0)
    emergence_factor = min(emergence_velocity / 2.0, 1.0)
    uniqueness = max(0.0, (50.0 - avg_cohort_size) / 50.0) if avg_cohort_size else 0.5

    resonance = (
        0.4 * cohort_connectedness +
        0.3 * pattern_richness +
        0.2 * emergence_factor +
        0.1 * uniqueness
    )

    existing = (await db.execute(
        select(UserResonance).where(UserResonance.user_id == user_id)
    )).scalar_one_or_none()
    if existing:
        existing.cohort_count = cohort_count
        existing.avg_cohort_size = avg_cohort_size
        existing.pattern_diversity = diversity
        existing.emergence_velocity = emergence_velocity
        existing.chart_uniqueness = float(uniqueness)
        existing.resonance_score = float(resonance)
    else:
        existing = UserResonance(
            user_id=user_id,
            cohort_count=cohort_count,
            avg_cohort_size=avg_cohort_size,
            pattern_diversity=diversity,
            emergence_velocity=emergence_velocity,
            chart_uniqueness=float(uniqueness),
            resonance_score=float(resonance),
        )
        db.add(existing)
    await db.commit()
    await db.refresh(existing)
    return existing


async def compute_resonance_for_all(db: AsyncSession) -> dict:
    """Refresh resonance for every consenting user. Called from the daily
    metrics job and ad-hoc rebuild endpoint.
    """
    user_ids = (await db.execute(
        select(User.id).where(User.hive_consent == True)  # noqa: E712
    )).scalars().all()
    n = 0
    for uid in user_ids:
        try:
            await compute_user_resonance(db, uid)
            n += 1
        except Exception as e:
            log.warning(f"[hive] resonance for {uid} failed: {e}")
    log.info(f"[hive] compute_resonance_for_all: refreshed={n}")
    return {"refreshed": n, "total": len(user_ids)}


# ---------------------------------------------------------------------------
# Phase 4: Memory-to-Theme flywheel
# ---------------------------------------------------------------------------

async def emerge_themes_from_memories(
    db: AsyncSession,
    user_id: str,
    memory_categories: tuple = ('insight', 'theme', 'pattern', 'active_thread'),
) -> dict:
    """When a user's memory synthesis lands, propagate notable memories
    into the cohorts they belong to as PatternTheme rows.

    Threshold: only memories in the eligible categories are propagated, and
    only if their content is at least 40 chars (filters out trivial notes).
    Each propagation either creates a new theme (initial confidence 0.3)
    or bumps the emergence_count of an existing similar theme in the same
    cohort (confidence += 0.1, capped at 1.0).

    "Similar" is currently exact content match. Phase 6 can add semantic
    matching via Supabase Vector if needed.
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.hive_consent:
        return {"created": 0, "bumped": 0}

    # User's components
    rows = (await db.execute(
        select(ChartComponent.component_type, ChartComponent.component_value)
        .join(ChartSignal, ChartSignal.signal_id == ChartComponent.signal_id)
        .where(ChartSignal.user_id == user_id)
    )).all()
    user_comps = {f"{t}={v}" for t, v in rows}
    if not user_comps:
        return {"created": 0, "bumped": 0}

    # Recent memories for this user, eligible categories only, content >= 40 chars
    week_ago = datetime.utcnow() - timedelta(days=2)
    mems = (await db.execute(
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .where(UserMemory.category.in_(memory_categories))
        .where(UserMemory.updated_at >= week_ago)
    )).scalars().all()
    eligible = [m for m in mems if m.content and len(m.content) >= 40
                and not m.connection_user_id  # never propagate connection memories
                and not m.connection_name]
    if not eligible:
        return {"created": 0, "bumped": 0}

    # Cohorts the user belongs to
    cohorts = (await db.execute(select(PatternCohort))).scalars().all()
    matched: list[PatternCohort] = []
    for c in cohorts:
        try:
            spec = json.loads(c.cohort_definition or '{}')
            filters = spec.get('filters', [])
            wanted = {f"{f['type']}={f['value']}" for f in filters}
            if wanted and wanted.issubset(user_comps):
                matched.append(c)
        except Exception:
            continue

    if not matched:
        return {"created": 0, "bumped": 0}

    created = 0
    bumped = 0
    for cohort in matched:
        for m in eligible:
            existing = (await db.execute(
                select(PatternTheme).where(
                    PatternTheme.cohort_id == cohort.cohort_id,
                    PatternTheme.theme_type == m.category,
                    PatternTheme.theme_content == m.content[:512],
                )
            )).scalar_one_or_none()
            if existing:
                existing.emergence_count += 1
                existing.emergence_confidence = min(1.0, float(existing.emergence_confidence) + 0.1)
                bumped += 1
            else:
                db.add(PatternTheme(
                    cohort_id=cohort.cohort_id,
                    theme_type=m.category,
                    theme_content=m.content[:512],
                    emergence_count=1,
                    emergence_confidence=0.3,
                ))
                created += 1

    await db.commit()
    return {"created": created, "bumped": bumped}


# ---------------------------------------------------------------------------
# Phase 5: Daily metrics
# ---------------------------------------------------------------------------

async def write_daily_hive_metrics(db: AsyncSession, response_lengths: Optional[list[int]] = None) -> HiveMetric:
    """Snapshot the hive's quality metrics for today. Idempotent per day."""
    total_users = (await db.execute(
        select(func.count(User.id)).where(User.hive_consent == True)  # noqa: E712
    )).scalar() or 0
    total_signals = (await db.execute(
        select(func.count(ChartSignal.signal_id))
    )).scalar() or 0

    cohorts = (await db.execute(select(PatternCohort))).scalars().all()
    active_cohorts = len(cohorts)
    avg_cohort = (sum(c.member_count for c in cohorts) // active_cohorts) if active_cohorts else 0
    high_conf = sum(1 for c in cohorts if c.confidence_score >= CONF_STRONG)

    cohort_ids = [c.cohort_id for c in cohorts]
    avg_themes = 0.0
    if cohort_ids:
        theme_count = (await db.execute(
            select(func.count(PatternTheme.theme_id))
            .where(PatternTheme.cohort_id.in_(cohort_ids))
        )).scalar() or 0
        avg_themes = float(theme_count) / max(1, active_cohorts)

    strong_corrs = (await db.execute(
        select(func.count(PatternCorrelation.correlation_id))
        .where(PatternCorrelation.correlation_strength >= CORR_STRONG)
    )).scalar() or 0

    avg_resonance = (await db.execute(
        select(func.avg(UserResonance.resonance_score))
    )).scalar() or 0.0

    median_len = 0
    if response_lengths:
        s = sorted(response_lengths)
        median_len = s[len(s) // 2]

    today = date.today()
    existing = (await db.execute(
        select(HiveMetric).where(HiveMetric.metric_date == today)
    )).scalar_one_or_none()
    if existing:
        existing.total_users = total_users
        existing.total_signals = total_signals
        existing.active_cohorts = active_cohorts
        existing.avg_cohort_size = avg_cohort
        existing.cohorts_high_confidence = high_conf
        existing.avg_themes_per_cohort = float(avg_themes)
        existing.strong_correlations = strong_corrs
        existing.avg_user_resonance = float(avg_resonance)
        existing.median_oracle_response_length = median_len
        row = existing
    else:
        row = HiveMetric(
            metric_date=today,
            total_users=total_users,
            total_signals=total_signals,
            active_cohorts=active_cohorts,
            avg_cohort_size=avg_cohort,
            cohorts_high_confidence=high_conf,
            avg_themes_per_cohort=float(avg_themes),
            strong_correlations=strong_corrs,
            avg_user_resonance=float(avg_resonance),
            median_oracle_response_length=median_len,
        )
        db.add(row)
    await db.commit()
    await db.refresh(row)
    log.info(f"[hive] daily metrics: users={total_users} cohorts={active_cohorts} high_conf={high_conf} corrs={strong_corrs}")
    return row


# ---------------------------------------------------------------------------
# Maintenance: prune signals for users who revoked consent
# ---------------------------------------------------------------------------

async def prune_non_consenting_signals(db: AsyncSession) -> int:
    """Delete signals for users who have hive_consent=False. Called by the
    daily maintenance job and on-demand when a user toggles consent off.
    Returns count deleted.
    """
    non_consenting = (await db.execute(
        select(User.id).where(User.hive_consent == False)  # noqa: E712
    )).scalars().all()
    if not non_consenting:
        return 0
    result = await db.execute(
        sql_delete(ChartSignal).where(ChartSignal.user_id.in_(non_consenting))
    )
    await db.commit()
    return getattr(result, 'rowcount', 0) or 0
