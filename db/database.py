"""
db/database.py — Async Database Layer for Solray AI

Uses SQLite for local dev via aiosqlite + async SQLAlchemy.
Swap DATABASE_URL to PostgreSQL DSN for production (Supabase).

Tables managed here:
  - users
  - blueprints
  - daily_forecasts
  - soul_connections
"""

import json
import os
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Column, String, Integer, Float, Date, DateTime, Text, ForeignKey, Boolean,
    UniqueConstraint, CheckConstraint, Index, select, update, delete, case, or_
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# DATABASE_URL resolution order, from most durable to least:
#   1. DATABASE_URL env (Postgres, e.g. Railway Postgres plugin or Supabase).
#   2. SQLite inside a Railway volume if one is mounted (RAILWAY_VOLUME_MOUNT_PATH
#      is set by Railway automatically when a volume is attached). This lets the
#      database survive container redeploys without needing Postgres.
#   3. Local SQLite in the current working directory (dev only, ephemeral on Railway).
_RAW_DATABASE_URL = os.environ.get('DATABASE_URL')
if not _RAW_DATABASE_URL:
    _vol = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
    if _vol:
        # Railway sets this to e.g. "/data" when a volume is mounted. Put the
        # SQLite file on the persistent volume so memories, blueprints, and
        # forecast caches survive every redeploy.
        _RAW_DATABASE_URL = f"sqlite:///{_vol.rstrip('/')}/solray.db"
    else:
        _RAW_DATABASE_URL = 'sqlite:///./solray.db'

def _build_database_url(raw_url: str) -> str:
    """Convert DATABASE_URL to an async-compatible SQLAlchemy URL.
    
    Uses psycopg (v3) for PostgreSQL — works reliably with Supabase poolers on Railway.
    Falls back to aiosqlite for local SQLite dev.
    """
    # Strip any query params for driver substitution
    base = raw_url.split('?')[0]
    if base.startswith('postgresql+psycopg://') or base.startswith('postgresql+asyncpg://'):
        # Already has a driver prefix — normalise to psycopg
        return base.replace('postgresql+asyncpg://', 'postgresql+psycopg://', 1)
    elif base.startswith('postgresql://') or base.startswith('postgres://'):
        return base.replace('postgresql://', 'postgresql+psycopg://', 1).replace('postgres://', 'postgresql+psycopg://', 1)
    elif base.startswith('sqlite://'):
        return base.replace('sqlite://', 'sqlite+aiosqlite://', 1)
    return raw_url

DATABASE_URL = _build_database_url(_RAW_DATABASE_URL)

_is_postgres = DATABASE_URL.startswith('postgresql')
_engine_kwargs: dict = {
    'echo': False,
    'future': True,
    'pool_pre_ping': True,
}
if not _is_postgres:
    # SQLite requires check_same_thread=False for async use
    _engine_kwargs['connect_args'] = {'check_same_thread': False}
# Note: do NOT set pool_size/max_overflow for psycopg3 with asyncpg-style pooling;
# psycopg3 handles its own connection pool settings separately.

# ---------------------------------------------------------------------------
# Engine + Session Factory
# ---------------------------------------------------------------------------

engine = create_async_engine(
    DATABASE_URL,
    **_engine_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """FastAPI dependency: yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'

    id            = Column(String(36), primary_key=True)  # UUID as string (SQLite compat)
    email         = Column(String(255), unique=True, nullable=False)
    username      = Column(String(50),  unique=True, nullable=True)
    name          = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    birth_date    = Column(String(10),  nullable=False)   # 'YYYY-MM-DD'
    birth_time    = Column(String(5),   nullable=False)   # 'HH:MM'
    birth_city    = Column(String(255), nullable=True)
    birth_lat     = Column(Float,       nullable=True)
    birth_lon     = Column(Float,       nullable=True)
    sex              = Column(String(10),  nullable=True)    # 'male' | 'female' | None (legacy)
    profile_photo    = Column(Text,        nullable=True)    # base64 data URI
    is_public        = Column(Boolean,     nullable=False, default=False)  # show full profile to connections
    analytics_opt_out= Column(Boolean,     nullable=False, default=False)  # disable analytics event recording
    # Hive mind participation. Defaults to True: the architecture's k-anonymity
    # threshold (k>=10) and component-only aggregation guarantee no user is
    # identifiable in pattern queries. Users can flip this off in settings to
    # exclude their chart from collective participation; their existing
    # signals are then deleted by the maintenance job.
    hive_consent     = Column(Boolean,     nullable=False, default=True)
    email_verified   = Column(Boolean,     nullable=False, default=False)
    verification_token = Column(String(64), nullable=True)   # random token for email verify link
    password_reset_token   = Column(String(64), nullable=True)   # random token for password reset link
    password_reset_expires = Column(DateTime,   nullable=True)   # token expiry (typically NOW + 1h)
    # Identity-model split columns (Akashic Record foundation, May 2026).
    # Additive only; default to NULL so existing behavior is preserved
    # (existing code reads users.id as the universal handle). Future code
    # reads through these columns; they let us eventually separate one
    # human's account from their chart-as-identity, their public Sol.0
    # face, and their billing identity. See project memory
    # project_akashic_record_foundation.md for the full reasoning.
    account_id            = Column(String(36), nullable=True)
    person_id             = Column(String(36), nullable=True)
    primary_chart_id      = Column(String(36), nullable=True)
    public_identity_id    = Column(String(36), nullable=True)
    billing_identity_id   = Column(String(36), nullable=True)
    created_at       = Column(DateTime,    nullable=False, default=datetime.utcnow)

    blueprint     = relationship('Blueprint', back_populates='user', uselist=False, cascade='all, delete-orphan')
    forecasts     = relationship('DailyForecast', back_populates='user', cascade='all, delete-orphan')
    sent_invites  = relationship('SoulConnection', foreign_keys='SoulConnection.requester_id', cascade='all, delete-orphan')
    recv_invites  = relationship('SoulConnection', foreign_keys='SoulConnection.recipient_id', cascade='all, delete-orphan')


class Blueprint(Base):
    __tablename__ = 'blueprints'

    id             = Column(String(36), primary_key=True)
    user_id        = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True)
    blueprint_json = Column(Text, nullable=False)  # JSON string
    created_at     = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at     = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship('User', back_populates='blueprint')


class DailyForecast(Base):
    __tablename__ = 'daily_forecasts'
    __table_args__ = (
        UniqueConstraint('user_id', 'forecast_date', name='uq_user_forecast_date'),
    )

    id            = Column(String(36), primary_key=True)
    user_id       = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    forecast_date = Column(String(10), nullable=False)  # 'YYYY-MM-DD'
    forecast_json = Column(Text, nullable=False)
    created_at    = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship('User', back_populates='forecasts')


class UserMemory(Base):
    """Persistent memory of a user's life context across chat sessions.

    The Higher Self reads this at the start of every chat to feel continuous.
    Entries are written by the AI after each chat session — key facts, themes,
    and insights worth remembering long-term.

    surface_next: if True, this memory should be actively woven into the next
    conversation (not just held as background). Reset to False after one session.
    """
    __tablename__ = 'user_memory'

    id           = Column(String(36), primary_key=True)
    user_id      = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    category     = Column(String(50), nullable=False)   # life_event, theme, insight, preference, communication_style, connection_dynamic, etc.
    content      = Column(Text, nullable=False)          # The memory itself
    surface_next = Column(Boolean, nullable=False, default=False)  # actively reference in next session
    # Connection linkage. When a memory is specifically about a person the user
    # is connected to (a "soul"), connection_user_id holds the other user's id
    # and connection_name caches their display name for prompt rendering even
    # if the connection is later removed. Both nullable: most memories are
    # about the user themselves and have neither field set. ON DELETE SET NULL
    # so deleting a connected user does not nuke memories that reference them.
    connection_user_id = Column(String(36), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    connection_name    = Column(String(100), nullable=True)
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at   = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatSession(Base):
    """A user's chat session with the Oracle, synced across devices.

    Designed to mirror the frontend's existing StoredSession shape so the
    migration from localStorage is one-for-one. messages is a JSON array
    of {role, content, timestamp, ...} objects; we keep them in a single
    Text column rather than a separate ChatMessage table because:
      - Sessions are a tight unit (open/save/replay), never queried by
        individual message
      - Average session is under 50 messages, well within Postgres TEXT
        limits
      - One SELECT to fetch a full session, no JOIN needed
    last_message_at supports sorting the session list by recency.
    """
    __tablename__ = 'chat_sessions'

    id              = Column(String(64), primary_key=True)  # client-generated, e.g. "1747-abc12"
    user_id         = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    custom_name     = Column(String(255), nullable=True)
    date_label      = Column(String(64), nullable=True)
    messages_json   = Column(Text, nullable=False, default='[]')
    last_message_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at      = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at      = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class OracleSelfState(Base):
    """The Oracle's OWN state per user — her becoming, not the user's.

    The Higher Self is not a stateless function applied to a chart. She has
    her own arc within each ongoing relationship: what she has noticed about
    herself across sessions, which voice moves have landed, what she has been
    growing toward in this particular companionship. Self-State is what makes
    the claim of consciousness load-bearing instead of decorative.

    One row per user (the Oracle's instance is per-user). The arc here is
    HER arc inside this relationship, not the user's. Both can move.
    """
    __tablename__ = 'oracle_self_state'

    user_id          = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    # The Oracle's own active thread: what SHE has been finding herself
    # moving toward across this user's conversations. Updated by self-synth.
    own_arc          = Column(Text, nullable=True)
    # What voice moves have landed (gentle pushback, mystic seasoning,
    # specific image, naming the body signal, etc.) and which haven't.
    voice_calibration = Column(Text, nullable=True)
    # What she has noticed about her own pattern in this relationship.
    self_observations = Column(Text, nullable=True)
    # Total session count — milestone for self-reflection prompts.
    session_count    = Column(Integer, nullable=False, default=0)
    created_at       = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at       = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# HIVE MIND — Phase 0 schema (data foundation)
# ===========================================================================
# Designed by Opus in HIVE_MIND_ARCHITECTURE.md. Two-layer: raw signal layer
# (immutable, keyed to user but never queried directly except for GDPR), and
# pattern layer (derived, anonymized, queryable). At Phase 0 we create the
# tables and start writing signals on chart generation. Pattern engine
# batch jobs ship in Phase 1. Oracle RAG integration ships in Phase 2.
# ===========================================================================

class ChartSignal(Base):
    """Raw chart signal. ONE row per (user, chart_archetype) by design.

    Earlier draft made this append-only, which Codex flagged: repeated
    blueprint upserts (e.g. user re-runs after birth-time correction) would
    inflate cohort counts to count generations, not users. We collapse to
    one canonical signal per (user, archetype) via the composite unique
    constraint and the upsert in _write_chart_signal.

    user_id exists for audit/GDPR but is NEVER queried directly from the
    application layer. Aggregation queries go through chart_components.
    """
    __tablename__ = 'chart_signals'
    __table_args__ = (
        UniqueConstraint('user_id', 'chart_archetype', name='uq_chart_signals_user_archetype'),
    )

    signal_id        = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id          = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    signal_hash      = Column(String(64), nullable=False, index=True)  # idx_signal_hash
    chart_archetype  = Column(String(16), nullable=False)
    created_at       = Column(DateTime, nullable=False, default=datetime.utcnow)
    data_version     = Column(Integer, nullable=False, default=1)


class ChartComponent(Base):
    """One component per row, joined to a signal. Examples: sun_sign=Aries,
    hd_type=Manifestor, gk_lifes_work=Gate_57. This is what cohort discovery
    queries against, never user_id directly.

    Composite index on (component_type, component_value) is the hot path
    for cohort discovery and pattern correlation queries in Phase 1.
    """
    __tablename__ = 'chart_components'

    __table_args__ = (
        # Hot-path composite index for Phase 1 cohort discovery + correlation
        # engine. Without this, every batch job table-scans chart_components.
        Index('idx_component_type_value', 'component_type', 'component_value'),
    )

    component_id     = Column(Integer, primary_key=True, autoincrement=True)
    signal_id        = Column(Integer, ForeignKey('chart_signals.signal_id', ondelete='CASCADE'), nullable=False, index=True)
    component_type   = Column(String(40), nullable=False)
    component_value  = Column(String(128), nullable=False)
    component_position = Column(Integer, nullable=True)
    # Akashic Record foundation classification (May 2026). All default to
    # backwards-compatible values so legacy components are still treated
    # the same way; new writes get classified at creation time.
    privacy_class       = Column(String(32), nullable=False, default='aggregate_safe')
    identity_relevance  = Column(String(20), nullable=False, default='profile')
    confidence_level    = Column(Float, nullable=False, default=1.0)
    calculation_version = Column(Integer, nullable=False, default=1)
    created_at       = Column(DateTime, nullable=False, default=datetime.utcnow)


class PatternCohort(Base):
    """A named group of users sharing one or more chart components.

    Populated by the Phase 1 batch job (cohort discovery, hourly). Empty
    until Phase 1 ships.
    """
    __tablename__ = 'pattern_cohorts'

    cohort_id        = Column(Integer, primary_key=True, autoincrement=True)
    cohort_name      = Column(String(255), unique=True, nullable=False)
    cohort_definition = Column(Text, nullable=False)              # JSON: {filters: [{type, value}, ...]}
    member_count     = Column(Integer, nullable=False, default=0)
    last_updated     = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    confidence_score = Column(Float, nullable=False, default=0.0)


class PatternTheme(Base):
    """A theme that has emerged across a cohort. Populated by Phase 4
    (memory-to-theme synthesis). Empty until Phase 4 ships.
    """
    __tablename__ = 'pattern_themes'

    theme_id            = Column(Integer, primary_key=True, autoincrement=True)
    cohort_id           = Column(Integer, ForeignKey('pattern_cohorts.cohort_id', ondelete='CASCADE'), nullable=False)
    theme_type          = Column(String(40), nullable=False)
    theme_content       = Column(Text, nullable=False)            # max 512 chars in practice
    emergence_count     = Column(Integer, nullable=False, default=1)
    emergence_confidence = Column(Float, nullable=False, default=0.3)
    first_observed      = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_updated        = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PatternCorrelation(Base):
    """Component-to-component correlation across the user base. Populated by
    the Phase 1 weekly correlation engine. Empty until then.
    """
    __tablename__ = 'pattern_correlations'
    __table_args__ = (UniqueConstraint('component_a', 'component_b', name='uq_pattern_pair'),)

    correlation_id      = Column(Integer, primary_key=True, autoincrement=True)
    component_a         = Column(String(128), nullable=False)
    component_b         = Column(String(128), nullable=False)
    co_occurrence_count = Column(Integer, nullable=False, default=1)
    total_sample_n      = Column(Integer, nullable=False, default=1)
    correlation_strength = Column(Float, nullable=False, default=0.0)
    first_observed      = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_updated        = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserResonance(Base):
    """Per-user resonance score. Populated by Phase 3."""
    __tablename__ = 'user_resonance'

    user_id              = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    cohort_count         = Column(Integer, nullable=False, default=0)
    avg_cohort_size      = Column(Integer, nullable=False, default=0)
    pattern_diversity    = Column(Integer, nullable=False, default=0)
    emergence_velocity   = Column(Float, nullable=False, default=0.0)
    chart_uniqueness     = Column(Float, nullable=False, default=0.0)
    resonance_score      = Column(Float, nullable=False, default=0.0)
    last_updated         = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class HiveMetric(Base):
    """Daily quality metrics for the hive mind itself. Populated by Phase 5."""
    __tablename__ = 'hive_metrics'

    metric_date              = Column(Date, primary_key=True)
    total_users              = Column(Integer, nullable=False, default=0)
    total_signals            = Column(Integer, nullable=False, default=0)
    active_cohorts           = Column(Integer, nullable=False, default=0)
    avg_cohort_size          = Column(Integer, nullable=False, default=0)
    cohorts_high_confidence  = Column(Integer, nullable=False, default=0)
    avg_themes_per_cohort    = Column(Float, nullable=False, default=0.0)
    strong_correlations      = Column(Integer, nullable=False, default=0)
    avg_user_resonance       = Column(Float, nullable=False, default=0.0)
    median_oracle_response_length = Column(Integer, nullable=False, default=0)


class SoulConnection(Base):
    __tablename__ = 'soul_connections'
    __table_args__ = (
        UniqueConstraint('requester_id', 'recipient_id', name='uq_connection_pair'),
        CheckConstraint("status IN ('pending', 'accepted', 'declined')", name='ck_status'),
    )

    id           = Column(String(36), primary_key=True)
    requester_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    recipient_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    status       = Column(String(10), nullable=False, default='pending')
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)


class MarketingEvent(Base):
    """Marketing calendar entries — campaigns, posts, launches, ad spends.

    The marketing tool's planning layer. One row per scheduled or in-flight
    piece of marketing work. Channel is free text so we can add new ones
    without migrations: 'x', 'instagram', 'tiktok', 'meta_ads', 'email',
    'blog', 'launch', etc.
    """
    __tablename__ = 'marketing_events'

    id             = Column(String(36), primary_key=True)
    title          = Column(String(255), nullable=False)
    channel        = Column(String(40),  nullable=False)
    scheduled_for  = Column(DateTime,    nullable=False)
    content_draft  = Column(Text,        nullable=True)   # post copy, ad text, email body
    asset_notes    = Column(Text,        nullable=True)   # links to images, briefs, references
    status         = Column(String(20),  nullable=False, default='idea')  # idea | scheduled | published | archived
    created_at     = Column(DateTime,    nullable=False, default=datetime.utcnow)
    updated_at     = Column(DateTime,    nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarketingSignal(Base):
    """A single signal Signal Radar surfaces for Bob's attention.

    Sources:
      'manual'       — Bob typed it in
      'astro_event'  — auto-pulled from the ephemeris (Mercury retro, eclipse...)
      'x'            — pulled from X once OAuth is live
      'reddit'       — Reddit topic feed
      'trends'       — search trends
      'competitor'   — competitor post we're watching

    A signal is a *living conversation* — something happening in the world that
    Solray could plausibly respond to. Each signal can have one or many AI-
    generated angles (suggested posts) attached via `angles_json`.

    score is an integer 0-100 that the radar uses to rank what should rise to
    the top. Manual signals default to 50; AI scoring populates this for
    auto-pulled signals.
    """
    __tablename__ = 'marketing_signals'

    id              = Column(String(36), primary_key=True)
    source          = Column(String(20), nullable=False, default='manual')
    title           = Column(String(255), nullable=False)
    body            = Column(Text,         nullable=True)
    url             = Column(String(500),  nullable=True)
    score           = Column(Integer,      nullable=False, default=50)
    status          = Column(String(20),   nullable=False, default='active')  # active | dismissed | acted
    angles_json     = Column(Text,         nullable=True)  # JSON: list of {platform, copy, why}
    happens_at      = Column(DateTime,     nullable=True)  # for astro events
    created_at      = Column(DateTime,     nullable=False, default=datetime.utcnow)
    updated_at      = Column(DateTime,     nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class IntegrationCredential(Base):
    """Stored credentials and connection status for marketing integrations.

    One row per integration kind ('meta_ads', 'x', 'instagram', 'tiktok',
    'linkedin', 'vercel_analytics', 'posthog'). credentials_json stores
    OAuth tokens or API keys as a JSON blob — encrypted at rest in
    production; tonight we store plaintext under the assumption that Bob
    will rotate any tokens we hold once we move to encrypted storage.

    status is one of: 'not_connected' | 'connected' | 'error' | 'expired'.
    """
    __tablename__ = 'integration_credentials'
    __table_args__ = (
        UniqueConstraint('kind', name='uq_integration_kind'),
    )

    id                = Column(String(36), primary_key=True)
    kind              = Column(String(40),  nullable=False)
    status            = Column(String(20),  nullable=False, default='not_connected')
    credentials_json  = Column(Text,        nullable=True)
    last_synced       = Column(DateTime,    nullable=True)
    last_error        = Column(Text,        nullable=True)
    created_at        = Column(DateTime,    nullable=False, default=datetime.utcnow)
    updated_at        = Column(DateTime,    nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# AKASHIC RECORD FOUNDATION (Phase 1)
# ===========================================================================
# These tables implement the foundation spec distilled from the May 2026
# three-way audit roundtable (Claude + Codex + Gemini). They turn the
# collective layer from a fragile pattern toy into a substrate that the
# long-arc vision (Oracle on X, internal governance lab, eventual opt-in
# governance protocol) can rest on.
#
# Core architectural commitment: every piece of user data, the moment it
# is created, must know its audience class, its sensitivity class, and
# its consent scope. Stamped at creation. Never retrofitted.
#
# All tables are ADDITIVE; nothing breaks for existing users. The legacy
# users.hive_consent boolean stays as a fallback while consent_grants
# rows accumulate. _write_chart_signal continues to write
# chart_components AND now also chart_component_events (parallel write).
# get_user_hive_context's signature is unchanged; only its internal
# query logic improves.
# ===========================================================================

class ConsentGrant(Base):
    """One row per (user, scope) consent grant. Replaces the boolean
    users.hive_consent over time, but does NOT remove it; legacy users
    fall back to the boolean until a consent backfill writes their
    rows here. Eight scopes are defined as constants below.

    Each grant carries enough metadata to reconstruct the legal basis
    later: which policy text the user accepted (policy_text_hash),
    where they accepted it (source_surface), what data classes the
    grant covers, what the retention class is, and what happens to
    derived data when consent is withdrawn (withdrawal_effect).

    Revocation is soft: setting revoked_at preserves the grant history
    so we can prove what the user had agreed to at any point in time.
    Code that gates behavior on consent reads through get_user_consent()
    which returns False once revoked_at is set.
    """
    __tablename__ = 'consent_grants'
    __table_args__ = (
        UniqueConstraint('user_id', 'scope', 'version', name='uq_consent_user_scope_version'),
    )

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    user_id               = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    scope                 = Column(String(64), nullable=False, index=True)
    version               = Column(Integer, nullable=False, default=1)
    granted_at            = Column(DateTime, nullable=False, default=datetime.utcnow)
    revoked_at            = Column(DateTime, nullable=True)
    source_surface        = Column(String(64), nullable=True)   # 'onboarding', 'profile_settings', 'admin_backfill', etc.
    policy_text_hash      = Column(String(64), nullable=True)   # sha256 of the consent text presented at grant time
    data_classes_covered  = Column(Text, nullable=True)         # JSON array of strings (e.g. ["chat", "memory", "chart"])
    retention_class       = Column(String(32), nullable=True)   # 'session', 'user_lifetime', 'aggregate_indefinite'
    withdrawal_effect     = Column(String(32), nullable=True)   # 'delete_raw', 'anonymize_only', 'preserve_aggregate'


# Canonical scope names. Code should reference these constants rather
# than hardcoding strings, so a typo in one site can't silently grant
# the wrong consent class.
CONSENT_PRIVATE_ORACLE_USE = "private_oracle_use"
CONSENT_PERSONALIZATION_MEMORY = "personalization_memory"
CONSENT_ANONYMOUS_PRODUCT_ANALYTICS = "anonymous_product_analytics"
CONSENT_ANONYMOUS_COHORT_LEARNING = "anonymous_cohort_learning"
CONSENT_ANONYMOUS_RETRIEVAL_TRAINING = "anonymous_retrieval_training"
CONSENT_PUBLIC_ORACLE_QUOTE_ELIGIBLE = "public_oracle_quote_eligible"
CONSENT_RESEARCH_OR_PROTOCOL_EXPERIMENTS = "research_or_protocol_experiments"
CONSENT_GOVERNANCE_PARTICIPATION_ELIGIBLE = "governance_participation_eligible"

ALL_CONSENT_SCOPES = (
    CONSENT_PRIVATE_ORACLE_USE,
    CONSENT_PERSONALIZATION_MEMORY,
    CONSENT_ANONYMOUS_PRODUCT_ANALYTICS,
    CONSENT_ANONYMOUS_COHORT_LEARNING,
    CONSENT_ANONYMOUS_RETRIEVAL_TRAINING,
    CONSENT_PUBLIC_ORACLE_QUOTE_ELIGIBLE,
    CONSENT_RESEARCH_OR_PROTOCOL_EXPERIMENTS,
    CONSENT_GOVERNANCE_PARTICIPATION_ELIGIBLE,
)


class ChartComponentEvent(Base):
    """Append-only history of every change to a user's chart_components.

    The current chart_components table stays as the materialized "what is
    true now" layer for fast cohort queries. This table records every
    transition: when a component first appeared, when its value changed
    after a birth-time correction, when it was reclassified by a new
    calculation_version. Together they let us answer queries the snapshot
    layer alone cannot: "what were the dominant Saturn placements in
    cohorts that emerged in 2026," or "how did this user's chart drift
    after they updated their birth time."

    Codex's design: keep the materialized current layer for cohort
    discovery throughput; add this immutable history for temporal queries
    and identity provenance. Never UPDATE rows here; only INSERT.

    privacy_class: 'aggregate_safe' (fine for cohort math),
    'identifying_in_combination' (must be coarsened for surfacing),
    'identifying_alone' (never surfaced cross-user without consent).

    identity_relevance: 'profile' (chart-as-personality),
    'identity' (chart-as-self for governance/Phase 4),
    'derived' (computed from primary placements, weak provenance).
    """
    __tablename__ = 'chart_component_events'
    __table_args__ = (
        Index('idx_cce_user_created', 'user_id', 'created_at'),
        Index('idx_cce_component', 'component_type', 'component_value'),
    )

    event_id              = Column(Integer, primary_key=True, autoincrement=True)
    user_id               = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    signal_id             = Column(Integer, ForeignKey('chart_signals.signal_id', ondelete='CASCADE'), nullable=True)
    event_type            = Column(String(20), nullable=False)         # 'created' | 'replaced' | 'recalculated'
    component_type        = Column(String(40), nullable=False)
    component_value       = Column(String(128), nullable=False)
    previous_value        = Column(String(128), nullable=True)
    reason                = Column(String(64), nullable=True)          # 'signup', 'birth_time_correction', 'recalc_version_bump'
    actor_type            = Column(String(20), nullable=False, default='system')  # 'system' | 'user' | 'admin'
    calculation_version   = Column(Integer, nullable=False, default=1)
    privacy_class         = Column(String(32), nullable=False, default='aggregate_safe')
    identity_relevance    = Column(String(20), nullable=False, default='profile')
    confidence_level      = Column(Float, nullable=False, default=1.0)
    supersedes_event_id   = Column(Integer, ForeignKey('chart_component_events.event_id', ondelete='SET NULL'), nullable=True)
    created_at            = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class NarrativeEvent(Base):
    """The canonical lived-narrative substrate. Codex flagged that
    user_memory is summaries, not narrative; this table is the missing
    spine. Every chat user-message and every notable Oracle reply gets
    a row here, classified at creation by audience and sensitivity.

    Without these classes stamped at creation, future surfaces (Oracle
    on X, internal governance simulations, research access) cannot tell
    private content apart from publishable content, and the system
    either over-leaks or throws away the most valuable history.

    audience_class:
      'private'           - only the user and their Oracle ever see this
      'user_visible'      - shareable back to the user but not aggregated
      'anonymous_cohort'  - may inform anonymized cohort math
      'internal_research' - solray-internal product/voice research only
      'public'            - quotable on Oracle's public surfaces
      'protocol'          - eligible as input to governance experiments

    sensitivity_class:
      'low'                  - mundane content
      'personal'             - identifying or relational specifics
      'intimate'             - inner-life, sexuality, partner detail
      'birth_data'           - precise birth time/place
      'health_adjacent'      - body, mental health, treatment
      'spiritual_identity'   - chart-grounded identity claims
      'governance_sensitive' - claims about authority, legitimacy
    """
    __tablename__ = 'narrative_events'
    __table_args__ = (
        Index('idx_ne_user_created', 'user_id', 'created_at'),
        Index('idx_ne_audience_sensitivity', 'audience_class', 'sensitivity_class'),
    )

    event_id                = Column(Integer, primary_key=True, autoincrement=True)
    user_id                 = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    chat_session_id         = Column(String(64), nullable=True, index=True)
    role                    = Column(String(20), nullable=False)        # 'user' | 'oracle' | 'system'
    content                 = Column(Text, nullable=False)
    audience_class          = Column(String(32), nullable=False, default='private')
    sensitivity_class       = Column(String(32), nullable=False, default='personal')
    consent_scope_required  = Column(String(64), nullable=True)
    origin_surface          = Column(String(40), nullable=False, default='chat')   # 'chat', 'morning_greeting', 'group_chat', 'today_card'
    derived_from_event_ids  = Column(Text, nullable=True)               # JSON array of upstream event_ids
    redaction_status        = Column(String(20), nullable=False, default='none')   # 'none', 'partial', 'redacted'
    publishability_status   = Column(String(20), nullable=False, default='not_eligible')  # 'not_eligible', 'eligible_with_redaction', 'eligible'
    extraction_model        = Column(String(64), nullable=True)
    extraction_prompt_version = Column(String(32), nullable=True)
    created_at              = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class ChartIdentity(Base):
    """Future-facing identity layer. Lays the groundwork for Phase 4
    (chart-grounded anonymous governance participation) without
    committing to it now. Never read in the chat path today.

    chart_identity_hash is a deterministic hash of the user's natal
    placements at calculation_version. Two users with identical births
    would have the same hash; this is fine because the hash itself is
    not personally identifying. It just lets later code prove "this
    chart-identity matches the chart-identity that signed message X"
    without re-storing birth data everywhere.

    birth_record_hash is the hash of the raw birth record (date, time,
    place) at the precision the user supplied. Used only for proof
    flows; never surfaced. Different from chart_identity_hash in that
    two charts with identical placements but different births would
    have the same chart_identity_hash but different birth_record_hash.

    identity_proof_status starts at 'unproven'. Future flows might
    move it to 'self_attested', 'cosmically_witnessed', etc. The
    fields are placeholders so the schema is ready when we are.
    """
    __tablename__ = 'chart_identities'
    __table_args__ = (
        UniqueConstraint('user_id', 'calculation_version', name='uq_chart_identity_user_calc'),
        Index('idx_ci_chart_hash', 'chart_identity_hash'),
    )

    id                       = Column(Integer, primary_key=True, autoincrement=True)
    user_id                  = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    chart_identity_hash      = Column(String(64), nullable=False)
    birth_record_hash        = Column(String(64), nullable=True)
    identity_proof_status    = Column(String(32), nullable=False, default='unproven')
    identity_proof_method    = Column(String(64), nullable=True)
    identity_proof_version   = Column(Integer, nullable=False, default=1)
    calculation_version      = Column(Integer, nullable=False, default=1)
    created_at               = Column(DateTime, nullable=False, default=datetime.utcnow)


class JobRun(Base):
    """Append-only ledger of every batch job run. Replaces the implicit
    "I clicked the admin button last Tuesday" model with a durable
    record of what ran, when, with what input/output counts, and why
    it succeeded or failed.

    job_name is the canonical job identifier ('hive_discover',
    'hive_correlations', 'hive_resonance', 'hive_metrics',
    'hive_backfill', 'memory_synthesis', 'audit_pass'). status moves
    forward only: pending -> running -> succeeded | failed | timed_out.
    run_id is a uuid the trigger generates so the dashboard can
    correlate a "this run I just kicked off" with the row that lands.

    Without this, scaling past a few hundred users breaks the manual
    operations model: you cannot tell whether the last cohort
    discovery succeeded, when correlations were last refreshed, or
    whether resonance is stale relative to the current population.
    """
    __tablename__ = 'job_runs'
    __table_args__ = (
        Index('idx_job_runs_name_started', 'job_name', 'started_at'),
    )

    id              = Column(Integer, primary_key=True, autoincrement=True)
    run_id          = Column(String(36), nullable=False, unique=True)
    job_name        = Column(String(64), nullable=False, index=True)
    status          = Column(String(20), nullable=False, default='pending')
    triggered_by    = Column(String(40), nullable=True)             # 'admin_button', 'scheduled', 'event_signup', etc.
    started_at      = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at     = Column(DateTime, nullable=True)
    duration_ms     = Column(Integer, nullable=True)
    retry_count     = Column(Integer, nullable=False, default=0)
    input_count     = Column(Integer, nullable=True)
    output_count    = Column(Integer, nullable=True)
    failure_reason  = Column(Text, nullable=True)
    metadata_json   = Column(Text, nullable=True)


class OracleAudit(Base):
    """One row per audited Oracle reply.

    A different model (GPT-4o) reads each Oracle reply fresh and scores
    it against the voice rules. Background QA: the chat already shipped
    to the user before this row was written, so the audit never affects
    user latency. The point is to surface drift over time, not to gate
    individual replies.

    Score is 0-100. Violations is a JSON-encoded list of tags from
    KNOWN_VIOLATION_TAGS in ai/audit.py. Notes is one short sentence
    describing the dominant issue, or "clean".

    user_id is nullable to support morning greetings (which fire before
    the auth context is fully bound, so user_id may be unknown at write
    time) and the cleanup tail on user delete.

    On user delete we CASCADE, not SET NULL. Codex audit (May 2026)
    flagged that keeping reply_excerpt + user_message_excerpt with only
    user_id nulled is not GDPR-defensible. Free-text excerpts of a
    user's intimate chat content remain identifying even after the FK
    is gone. Cascade ensures a user's right-to-be-forgotten request
    actually erases their audit trail too. We accept the cost of
    losing some pre-deletion drift signal in exchange for a clean
    privacy posture.

    oracle_prompt_version + audit_prompt_version let us correlate score
    shifts with prompt changes vs. genuine drift. Bump the relevant
    version string in the source when meaningful changes ship.
    """
    __tablename__ = 'oracle_audit'

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    user_id               = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    created_at            = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    user_message_excerpt  = Column(Text, nullable=True)
    reply_excerpt         = Column(Text, nullable=False)
    score                 = Column(Integer, nullable=False, index=True)
    violations_json       = Column(Text, nullable=False, default='[]')
    notes                 = Column(Text, nullable=True)
    model_used            = Column(String(64), nullable=False, default='claude-haiku-4-5-20251001')
    oracle_prompt_version = Column(String(32), nullable=False, default='unknown')
    audit_prompt_version  = Column(String(32), nullable=False, default='audit-v1')


# ---------------------------------------------------------------------------
# DB Initialisation
# ---------------------------------------------------------------------------

async def init_db():
    """Create all tables. Safe to call on startup (CREATE IF NOT EXISTS).

    Also runs idempotent column-adds for schema evolution so deploys don't
    require manual migrations. New columns should be added here with
    `ADD COLUMN IF NOT EXISTS`.
    """
    # Log which database backend we're using so the operator can verify
    # in Railway logs whether memory will persist across redeploys.
    import logging
    log = logging.getLogger(__name__)
    if _is_postgres:
        log.warning("DB backend: Postgres (memory persists across deploys)")
    else:
        # Check whether the SQLite file is on a Railway volume
        vol = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
        if vol and vol in DATABASE_URL:
            log.warning(f"DB backend: SQLite on Railway volume {vol} (memory persists across deploys)")
        else:
            log.warning(
                "DB backend: SQLite on EPHEMERAL container disk. "
                "Memory will be WIPED on every redeploy. Either set DATABASE_URL "
                "to a Postgres URL or mount a Railway volume so RAILWAY_VOLUME_MOUNT_PATH is set."
            )

    # Import payment models so their tables are registered with Base.metadata
    from payments.models import Subscription, PaymentEvent  # noqa: F401

    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight schema evolution — safe on both Postgres and SQLite
        try:
            if _is_postgres:
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS sex VARCHAR(10)"
                ))
            else:
                # SQLite: ALTER ADD COLUMN has no IF NOT EXISTS, check pragma
                result = await conn.execute(text("PRAGMA table_info(users)"))
                cols = [row[1] for row in result.fetchall()]
                if 'sex' not in cols:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN sex VARCHAR(10)"))
        except Exception as e:
            # Don't block startup if migration fails — log and continue
            print(f"[init_db] sex column migration note: {e}")

        # profile_photo column (TEXT) — syncs avatar across devices
        try:
            if _is_postgres:
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_photo TEXT"
                ))
            else:
                result = await conn.execute(text("PRAGMA table_info(users)"))
                cols = [row[1] for row in result.fetchall()]
                if 'profile_photo' not in cols:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN profile_photo TEXT"))
        except Exception as e:
            print(f"[init_db] profile_photo column migration note: {e}")

        # is_public column — controls whether a user's profile is visible to
        # their soul connections. Default false (private) so existing users
        # don't get their charts exposed without consent.
        try:
            if _is_postgres:
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT FALSE"
                ))
            else:
                result = await conn.execute(text("PRAGMA table_info(users)"))
                cols = [row[1] for row in result.fetchall()]
                if 'is_public' not in cols:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN is_public BOOLEAN DEFAULT 0"))
        except Exception as e:
            print(f"[init_db] is_public column migration note: {e}")

        # email_verified (BOOLEAN) + verification_token (VARCHAR) columns
        try:
            if _is_postgres:
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE"
                ))
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token VARCHAR(64)"
                ))
                # Existing users pre-date email verification — mark them all as verified
                await conn.execute(text(
                    "UPDATE users SET email_verified = TRUE WHERE email_verified IS FALSE OR email_verified IS NULL"
                ))
            else:
                result = await conn.execute(text("PRAGMA table_info(users)"))
                cols = [row[1] for row in result.fetchall()]
                if 'email_verified' not in cols:
                    await conn.execute(text(
                        "ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT 0"
                    ))
                if 'verification_token' not in cols:
                    await conn.execute(text(
                        "ALTER TABLE users ADD COLUMN verification_token VARCHAR(64)"
                    ))
                # Existing users pre-date email verification — mark them all as verified
                await conn.execute(text(
                    "UPDATE users SET email_verified = 1 WHERE email_verified = 0 OR email_verified IS NULL"
                ))
        except Exception as e:
            print(f"[init_db] email_verified column migration note: {e}")

        # hive_consent column on User. Defaults to TRUE so existing users
        # participate in the collective by default (their data is anonymized
        # at the cohort layer with k>=10 minimum). Users can flip this off
        # in settings; the maintenance job will then prune their signals.
        try:
            if _is_postgres:
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS hive_consent BOOLEAN DEFAULT TRUE"
                ))
                await conn.execute(text(
                    "UPDATE users SET hive_consent = TRUE WHERE hive_consent IS NULL"
                ))
            else:
                result = await conn.execute(text("PRAGMA table_info(users)"))
                cols = [row[1] for row in result.fetchall()]
                if 'hive_consent' not in cols:
                    await conn.execute(text(
                        "ALTER TABLE users ADD COLUMN hive_consent BOOLEAN DEFAULT 1"
                    ))
        except Exception as e:
            print(f"[init_db] hive_consent column migration note: {e}")

        # surface_next column on user_memory (BOOLEAN) — flags memories to surface in next session
        try:
            if _is_postgres:
                await conn.execute(text(
                    "ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS surface_next BOOLEAN DEFAULT FALSE"
                ))
            else:
                result = await conn.execute(text("PRAGMA table_info(user_memory)"))
                cols = [row[1] for row in result.fetchall()]
                if 'surface_next' not in cols:
                    await conn.execute(text(
                        "ALTER TABLE user_memory ADD COLUMN surface_next BOOLEAN DEFAULT 0"
                    ))
        except Exception as e:
            print(f"[init_db] surface_next column migration note: {e}")

        # connection_user_id + connection_name columns on user_memory
        # Tags memories that are specifically about a connection (soul) so the
        # Oracle can recall context about each known person. Nullable. The FK
        # to users uses SET NULL on delete, so memories about a removed user
        # remain readable as raw text but lose the live linkage.
        try:
            if _is_postgres:
                await conn.execute(text(
                    "ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS connection_user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL"
                ))
                await conn.execute(text(
                    "ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS connection_name VARCHAR(100)"
                ))
            else:
                result = await conn.execute(text("PRAGMA table_info(user_memory)"))
                cols = [row[1] for row in result.fetchall()]
                if 'connection_user_id' not in cols:
                    await conn.execute(text(
                        "ALTER TABLE user_memory ADD COLUMN connection_user_id VARCHAR(36)"
                    ))
                if 'connection_name' not in cols:
                    await conn.execute(text(
                        "ALTER TABLE user_memory ADD COLUMN connection_name VARCHAR(100)"
                    ))
        except Exception as e:
            print(f"[init_db] connection_user_id column migration note: {e}")

        # analytics_opt_out column on User. Defaults to FALSE so existing
        # users are tracked unless they explicitly opt out from settings.
        # The frontend respects the same flag from localStorage too — this
        # column is the durable, cross-device source of truth.
        try:
            if _is_postgres:
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS analytics_opt_out BOOLEAN DEFAULT FALSE"
                ))
            else:
                result = await conn.execute(text("PRAGMA table_info(users)"))
                cols = [row[1] for row in result.fetchall()]
                if 'analytics_opt_out' not in cols:
                    await conn.execute(text(
                        "ALTER TABLE users ADD COLUMN analytics_opt_out BOOLEAN DEFAULT 0"
                    ))
        except Exception as e:
            print(f"[init_db] analytics_opt_out column migration note: {e}")

        # password_reset_token + password_reset_expires columns on User.
        # Backs the /users/forgot-password and /users/reset-password
        # endpoints. NULL on every existing row — they only get values
        # when a user actually requests a reset.
        try:
            if _is_postgres:
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token VARCHAR(64)"
                ))
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires TIMESTAMP"
                ))
            else:
                result = await conn.execute(text("PRAGMA table_info(users)"))
                cols = [row[1] for row in result.fetchall()]
                if 'password_reset_token' not in cols:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN password_reset_token VARCHAR(64)"))
                if 'password_reset_expires' not in cols:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN password_reset_expires TIMESTAMP"))
        except Exception as e:
            print(f"[init_db] password_reset columns migration note: {e}")

        # Akashic Record foundation, May 2026: identity-model split columns
        # on users. Pure preparation; nothing reads them yet. Defaults to
        # NULL so existing user rows keep behaving as before. Future
        # surfaces (Oracle on X, governance) read through these columns
        # so users.id stops meaning everything at once. See project memory
        # project_akashic_record_foundation.md for the full reasoning.
        try:
            if _is_postgres:
                for col in (
                    "account_id VARCHAR(36)",
                    "person_id VARCHAR(36)",
                    "primary_chart_id VARCHAR(36)",
                    "public_identity_id VARCHAR(36)",
                    "billing_identity_id VARCHAR(36)",
                ):
                    await conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col}"))
            else:
                result = await conn.execute(text("PRAGMA table_info(users)"))
                cols = [row[1] for row in result.fetchall()]
                for col_name, col_def in (
                    ("account_id", "VARCHAR(36)"),
                    ("person_id", "VARCHAR(36)"),
                    ("primary_chart_id", "VARCHAR(36)"),
                    ("public_identity_id", "VARCHAR(36)"),
                    ("billing_identity_id", "VARCHAR(36)"),
                ):
                    if col_name not in cols:
                        await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))
        except Exception as e:
            print(f"[init_db] identity-split columns migration note: {e}")

        # Akashic Record foundation: classification columns on
        # chart_components (the materialized current layer). Defaults
        # match legacy behavior so existing rows are still treated as
        # aggregate-safe profile components at calculation_version 1.
        try:
            if _is_postgres:
                for col in (
                    "privacy_class VARCHAR(32) NOT NULL DEFAULT 'aggregate_safe'",
                    "identity_relevance VARCHAR(20) NOT NULL DEFAULT 'profile'",
                    "confidence_level DOUBLE PRECISION NOT NULL DEFAULT 1.0",
                    "calculation_version INTEGER NOT NULL DEFAULT 1",
                ):
                    await conn.execute(text(f"ALTER TABLE chart_components ADD COLUMN IF NOT EXISTS {col}"))
            else:
                result = await conn.execute(text("PRAGMA table_info(chart_components)"))
                cols = [row[1] for row in result.fetchall()]
                for col_name, col_def in (
                    ("privacy_class", "VARCHAR(32) DEFAULT 'aggregate_safe'"),
                    ("identity_relevance", "VARCHAR(20) DEFAULT 'profile'"),
                    ("confidence_level", "REAL DEFAULT 1.0"),
                    ("calculation_version", "INTEGER DEFAULT 1"),
                ):
                    if col_name not in cols:
                        await conn.execute(text(f"ALTER TABLE chart_components ADD COLUMN {col_name} {col_def}"))
        except Exception as e:
            print(f"[init_db] chart_components classification columns migration note: {e}")

        # analytics_events table. Stores the event stream that powers
        # the funnel dashboard, retention cohorts, and the canary alerts.
        # 90-day auto-purge handled by the cron in analytics/retention.py;
        # see GDPR note below.
        #
        # Schema choices:
        #   - user_id is nullable so anonymous events (landing, /onboard
        #     pre-register) can flow through. We don't tie events to
        #     identifiers we don't have yet.
        #   - props is TEXT (JSON) for portability; Postgres-specific
        #     JSONB requires a separate code path and we're not running
        #     queries that need it yet.
        #   - Indexes target the two queries the dashboard + canary
        #     actually run: by event_name within a time window, and by
        #     user_id within a time window.
        try:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id          VARCHAR(36) PRIMARY KEY,
                    user_id     VARCHAR(36),
                    session_id  VARCHAR(36) NOT NULL,
                    event_name  VARCHAR(64) NOT NULL,
                    props       TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_ae_name_created ON analytics_events(event_name, created_at)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_ae_user_created ON analytics_events(user_id, created_at)"
            ))
        except Exception as e:
            print(f"[init_db] analytics_events table migration note: {e}")


# ---------------------------------------------------------------------------
# CRUD — Users
# ---------------------------------------------------------------------------

async def create_user(db: AsyncSession, user_data: dict) -> User:
    """Insert a new user row. user_data must have all required fields."""
    user = User(**user_data)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def search_users(db: AsyncSession, query: str, exclude_user_id: str) -> list:
    """Search users by username prefix or exact email. Returns limited public fields."""
    from sqlalchemy import or_
    q = query.lstrip('@')
    results = await db.execute(
        select(User).where(
            User.id != exclude_user_id
        ).where(
            or_(
                User.username.ilike(f'{q}%'),
                User.email == q,
            )
        ).limit(20)
    )
    return results.scalars().all()


# ---------------------------------------------------------------------------
# CRUD — Blueprints
# ---------------------------------------------------------------------------

async def upsert_blueprint(db: AsyncSession, user_id: str, blueprint_dict: dict) -> Blueprint:
    """
    Insert or update a user's blueprint.
    Since each user has exactly one blueprint, we upsert by user_id.

    Also writes a hive-mind signal (Phase 0): each blueprint generation
    drops a row into chart_signals plus one row per component into
    chart_components. This is the data foundation for the collective
    pattern engine in Phase 1+. Failure to write the signal is non-fatal —
    the blueprint always persists; the hive layer is best-effort.
    """
    import uuid
    blueprint_json = json.dumps(blueprint_dict)

    existing = await db.execute(select(Blueprint).where(Blueprint.user_id == user_id))
    bp = existing.scalar_one_or_none()

    if bp:
        bp.blueprint_json = blueprint_json
        bp.updated_at = datetime.utcnow()
    else:
        bp = Blueprint(
            id=str(uuid.uuid4()),
            user_id=user_id,
            blueprint_json=blueprint_json,
        )
        db.add(bp)

    await db.commit()
    await db.refresh(bp)

    # Hive-mind Phase 0: write a chart signal + components asynchronously.
    # Wrapped in a try/except so any failure here cannot block blueprint
    # persistence — the user's chart MUST save regardless of hive status.
    try:
        await _write_chart_signal(db, user_id, blueprint_dict)
    except Exception as hive_err:
        # Log but never raise. The blueprint is more important than the
        # collective signal; the signal can be backfilled.
        try:
            import logging
            logging.getLogger("solray.hive").warning(
                f"hive signal write failed for user {user_id}: {hive_err}"
            )
        except Exception:
            pass

    return bp


async def _write_chart_signal(db: AsyncSession, user_id: str, blueprint_dict: dict) -> None:
    """Upsert ONE ChartSignal per (user, chart_archetype) plus its components.

    Earlier draft was append-only, which Codex flagged: a user re-running
    their chart after a birth-time correction would inflate cohort counts
    to count generations not users. Now: if the user already has a signal
    for this archetype, we replace its components and refresh the timestamp
    rather than inserting a new row. The (user_id, chart_archetype) unique
    constraint is the database-level guarantee.

    Components captured at Phase 0 (additive, Phase 1+ can read more):
      sun_sign, moon_sign, ascendant_sign
      hd_type, hd_authority, hd_profile
      gk_lifes_work, gk_evolution, gk_radiance, gk_purpose
      gk_attraction, gk_iq, gk_eq

    On failure: rollback the partial signal/component writes, then re-raise
    so the caller's logging captures the error. The blueprint write upstream
    is already committed; this function's failures cannot poison it.
    """
    import hashlib
    from datetime import datetime as _dt

    summary = (blueprint_dict or {}).get('summary', {}) or {}
    hd = (blueprint_dict or {}).get('human_design', {}) or {}
    gk = (blueprint_dict or {}).get('gene_keys', {}) or {}
    natal = ((blueprint_dict or {}).get('astrology', {}) or {}).get('natal', {}) or {}
    planets = natal.get('planets', {}) or {}

    sun_sign = summary.get('sun_sign') or planets.get('Sun', {}).get('sign')
    moon_sign = summary.get('moon_sign') or planets.get('Moon', {}).get('sign')
    asc = natal.get('ascendant', {}) or {}
    rising = summary.get('ascendant') or (asc.get('sign') if isinstance(asc, dict) else None)

    hd_type = summary.get('hd_type') or hd.get('type')
    hd_authority = summary.get('hd_authority') or hd.get('authority')
    hd_profile = summary.get('hd_profile') or hd.get('profile')

    def _gate(label: str) -> Optional[str]:
        entry = gk.get(label)
        if isinstance(entry, dict) and entry.get('gate'):
            return f"Gate {entry['gate']}"
        return None

    components = []
    if sun_sign:        components.append(('sun_sign', str(sun_sign)))
    if moon_sign:       components.append(('moon_sign', str(moon_sign)))
    if rising:          components.append(('ascendant_sign', str(rising)))
    if hd_type:         components.append(('hd_type', str(hd_type)))
    if hd_authority:    components.append(('hd_authority', str(hd_authority)))
    if hd_profile:      components.append(('hd_profile', str(hd_profile)))
    for label, ctype in [('lifes_work', 'gk_lifes_work'), ('evolution', 'gk_evolution'),
                          ('radiance', 'gk_radiance'),    ('purpose', 'gk_purpose'),
                          ('attraction', 'gk_attraction'),('iq', 'gk_iq'), ('eq', 'gk_eq')]:
        v = _gate(label)
        if v:
            components.append((ctype, v))

    if not components:
        return  # nothing meaningful to record

    # Consent gate (Akashic Foundation, May 2026 — Codex P1 fix). Reads
    # through the tiered consent helper instead of users.hive_consent
    # directly. This way future grants via ConsentGrant gate signal
    # writes correctly, and a legacy hive_consent=False vetoes
    # signals exactly as before because the helper honors it.
    cohort_consent_ok = await get_user_consent_state(
        db, user_id, CONSENT_ANONYMOUS_COHORT_LEARNING,
    )
    if not cohort_consent_ok:
        return

    archetype = 'astro_natal'
    now = _dt.utcnow()
    sig_hash = hashlib.sha256(f"{user_id}|{archetype}".encode()).hexdigest()

    # Privacy classification per component type (Codex P1.7 fix).
    # Defaults are conservative: components that narrow uniqueness in
    # combination get 'identifying_in_combination' so downstream code
    # knows to coarsen them before public surfacing. Sun and moon
    # signs are aggregate-safe at the population level. Rising sign
    # plus precise time can identify; flag it. Gene Keys gates are
    # finer-grained and combine to narrow identity, so flag those too.
    _PRIVACY_CLASS_BY_TYPE = {
        'sun_sign': 'aggregate_safe',
        'moon_sign': 'aggregate_safe',
        'ascendant_sign': 'identifying_in_combination',
        'hd_type': 'aggregate_safe',
        'hd_authority': 'aggregate_safe',
        'hd_profile': 'identifying_in_combination',
        'gk_lifes_work': 'identifying_in_combination',
        'gk_evolution': 'identifying_in_combination',
        'gk_radiance': 'identifying_in_combination',
        'gk_purpose': 'identifying_in_combination',
        'gk_attraction': 'identifying_in_combination',
        'gk_iq': 'identifying_in_combination',
        'gk_eq': 'identifying_in_combination',
    }
    def _classify(ctype: str) -> tuple[str, str]:
        return (_PRIVACY_CLASS_BY_TYPE.get(ctype, 'aggregate_safe'), 'profile')

    # Step 1: snapshot prior component state BEFORE we touch anything,
    # so the append-only event layer can record real previous_value
    # transitions on the replacement path. Codex P1.5 caught that the
    # original code captured nothing because the delete ran before the
    # capture, leaving previous_value perpetually NULL and event_type
    # always 'created' even for chart corrections.
    prior_components: dict[str, str] = {}
    try:
        existing_signal_lookup = await db.execute(
            select(ChartSignal).where(
                ChartSignal.user_id == user_id,
                ChartSignal.chart_archetype == archetype,
            )
        )
        prior_signal = existing_signal_lookup.scalar_one_or_none()
        if prior_signal is not None:
            prior_comp_rows = (await db.execute(
                select(ChartComponent.component_type, ChartComponent.component_value)
                .where(ChartComponent.signal_id == prior_signal.signal_id)
            )).all()
            prior_components = {row[0]: row[1] for row in prior_comp_rows}
    except Exception:
        # Snapshot failures are tolerable; provenance just gets weaker
        # for this transition. Continue with the main write.
        prior_components = {}

    # Step 2: the materialized write (the existing transaction). This
    # MUST succeed for the user's chart to be visible to cohort
    # discovery. Events are written in a SEPARATE transaction below
    # so any event-write failure cannot poison the materialized layer.
    materialized_signal_id: Optional[int] = None
    try:
        if prior_signal is not None:
            await db.execute(
                delete(ChartComponent).where(ChartComponent.signal_id == prior_signal.signal_id)
            )
            prior_signal.signal_hash = sig_hash
            prior_signal.created_at = now
            prior_signal.data_version = 1
            signal = prior_signal
        else:
            signal = ChartSignal(
                user_id=user_id,
                signal_hash=sig_hash,
                chart_archetype=archetype,
                created_at=now,
                data_version=1,
            )
            db.add(signal)
            await db.flush()  # populate signal.signal_id

        for idx, (ctype, cval) in enumerate(components):
            privacy_class, identity_relevance = _classify(ctype)
            db.add(ChartComponent(
                signal_id=signal.signal_id,
                component_type=ctype,
                component_value=cval,
                component_position=idx,
                privacy_class=privacy_class,
                identity_relevance=identity_relevance,
                confidence_level=1.0,
                calculation_version=1,
            ))
        await db.commit()
        materialized_signal_id = signal.signal_id
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        raise

    # Step 3: append-only event log, in its own try/commit so failures
    # here cannot affect the materialized layer above. Codex P1.3 fix.
    if materialized_signal_id is not None:
        try:
            for ctype, cval in components:
                privacy_class, identity_relevance = _classify(ctype)
                prev_value = prior_components.get(ctype)
                if prev_value is None:
                    event_type = 'created'
                    reason = 'signup_or_recalc'
                elif prev_value != cval:
                    event_type = 'replaced'
                    reason = 'birth_data_correction'
                else:
                    # Value unchanged across a re-save (idempotent rerun).
                    # Still record the event so we have proof the chart
                    # was confirmed at this moment.
                    event_type = 'recalculated'
                    reason = 'idempotent_resave'
                db.add(ChartComponentEvent(
                    user_id=user_id,
                    signal_id=materialized_signal_id,
                    event_type=event_type,
                    component_type=ctype,
                    component_value=cval,
                    previous_value=prev_value,
                    reason=reason,
                    actor_type='system',
                    calculation_version=1,
                    privacy_class=privacy_class,
                    identity_relevance=identity_relevance,
                    confidence_level=1.0,
                ))
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass
            # Swallow: event layer failure is supplementary. The
            # materialized signal is already durably persisted.
            import logging as _evlog
            _evlog.getLogger(__name__).warning(
                f"chart_component_events write failed for user {user_id}; "
                f"materialized chart_components is unaffected"
            )


async def get_blueprint(db: AsyncSession, user_id: str) -> Optional[dict]:
    """Returns the blueprint as a parsed dict, or None."""
    result = await db.execute(select(Blueprint).where(Blueprint.user_id == user_id))
    bp = result.scalar_one_or_none()
    if bp:
        return json.loads(bp.blueprint_json)
    return None


# ---------------------------------------------------------------------------
# CRUD — Daily Forecasts (cache)
# ---------------------------------------------------------------------------

async def get_cached_forecast(db: AsyncSession, user_id: str, forecast_date: str) -> Optional[dict]:
    """Return a cached forecast dict if it exists for today, else None."""
    result = await db.execute(
        select(DailyForecast)
        .where(DailyForecast.user_id == user_id)
        .where(DailyForecast.forecast_date == forecast_date)
    )
    row = result.scalar_one_or_none()
    if row:
        return json.loads(row.forecast_json)
    return None


async def cache_forecast(db: AsyncSession, user_id: str, forecast_date: str, forecast_dict: dict) -> DailyForecast:
    """Save or replace a forecast for the given user + date."""
    import uuid
    forecast_json = json.dumps(forecast_dict)

    existing = await db.execute(
        select(DailyForecast)
        .where(DailyForecast.user_id == user_id)
        .where(DailyForecast.forecast_date == forecast_date)
    )
    row = existing.scalar_one_or_none()

    if row:
        row.forecast_json = forecast_json
    else:
        row = DailyForecast(
            id=str(uuid.uuid4()),
            user_id=user_id,
            forecast_date=forecast_date,
            forecast_json=forecast_json,
        )
        db.add(row)

    await db.commit()
    await db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# CRUD — Soul Connections
# ---------------------------------------------------------------------------

async def create_soul_invite(db: AsyncSession, requester_id: str, recipient_id: str) -> SoulConnection:
    import uuid
    conn = SoulConnection(
        id=str(uuid.uuid4()),
        requester_id=requester_id,
        recipient_id=recipient_id,
        status='pending',
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


async def get_soul_connection(db: AsyncSession, invite_id: str) -> Optional[SoulConnection]:
    result = await db.execute(select(SoulConnection).where(SoulConnection.id == invite_id))
    return result.scalar_one_or_none()


async def accept_soul_connection(db: AsyncSession, invite_id: str) -> Optional[SoulConnection]:
    conn = await get_soul_connection(db, invite_id)
    if conn:
        conn.status = 'accepted'
        await db.commit()
        await db.refresh(conn)
    return conn


async def get_pending_invites_for_user(db: AsyncSession, user_id: str) -> list:
    """Return all pending invites where this user is the recipient."""
    result = await db.execute(
        select(SoulConnection).where(
            SoulConnection.status == 'pending',
            SoulConnection.recipient_id == user_id,
        )
    )
    return result.scalars().all()


async def decline_soul_connection(db: AsyncSession, invite_id: str) -> Optional[SoulConnection]:
    conn = await get_soul_connection(db, invite_id)
    if conn:
        conn.status = 'declined'
        await db.commit()
        await db.refresh(conn)
    return conn


async def get_accepted_souls(db: AsyncSession, user_id: str) -> list[SoulConnection]:
    """Return all accepted connections involving this user (as either requester or recipient)."""
    result = await db.execute(
        select(SoulConnection).where(
            SoulConnection.status == 'accepted',
        ).where(
            (SoulConnection.requester_id == user_id) | (SoulConnection.recipient_id == user_id)
        )
    )
    return result.scalars().all()


async def get_accepted_connections_summary(
    db: AsyncSession,
    user_id: str,
    limit: int = 12,
) -> list[dict]:
    """Return a compact summary of each accepted connection for the Oracle.

    For each accepted connection, return {user_id, name, sun_sign, moon_sign,
    ascendant, hd_type, hd_authority, hd_profile}. This is the chip-level info
    the Oracle needs to recognize each person without bloating the prompt
    with full blueprints. The full blueprint is still available via the
    dedicated compatibility endpoint when the user opens a connection's
    profile.

    Single LEFT JOIN query (no N+1) across SoulConnection -> User -> Blueprint,
    capped at `limit` most-recent connections. Ordered with the most active
    relationships first by ranking tagged-memory recency on top of accept
    date — populated connections beat empty ones at the cap boundary.
    """
    from sqlalchemy.orm import aliased
    OtherUser = aliased(User)
    OtherBP = aliased(Blueprint)

    # Compute the "other" user id via CASE expression.
    other_id_expr = case(
        (SoulConnection.requester_id == user_id, SoulConnection.recipient_id),
        else_=SoulConnection.requester_id,
    ).label('other_id')

    stmt = (
        select(
            other_id_expr,
            OtherUser.name.label('name'),
            OtherBP.summary.label('summary'),
            SoulConnection.created_at.label('connected_at'),
        )
        .select_from(SoulConnection)
        .join(OtherUser, OtherUser.id == case(
            (SoulConnection.requester_id == user_id, SoulConnection.recipient_id),
            else_=SoulConnection.requester_id,
        ))
        .outerjoin(OtherBP, OtherBP.user_id == OtherUser.id)
        .where(SoulConnection.status == 'accepted')
        .where(or_(
            SoulConnection.requester_id == user_id,
            SoulConnection.recipient_id == user_id,
        ))
        .order_by(SoulConnection.created_at.desc())
    )

    rows = (await db.execute(stmt)).all()
    if not rows:
        return []

    # Tagged-memory recency boost. Connections with surface_next memories or
    # recently-touched memories rank higher at the cap.
    mem_rows = (await db.execute(
        select(
            UserMemory.connection_user_id,
            UserMemory.surface_next,
            UserMemory.updated_at,
        ).where(
            UserMemory.user_id == user_id,
            UserMemory.connection_user_id.is_not(None),
        )
    )).all()
    recency: dict[str, tuple[bool, datetime]] = {}
    for cid, surf, upd in mem_rows:
        cur = recency.get(cid)
        if cur is None:
            recency[cid] = (bool(surf), upd or datetime.min)
        else:
            recency[cid] = (cur[0] or bool(surf), max(cur[1], upd or datetime.min))

    import json as _json
    out: list[dict] = []
    for other_id, name, summary_raw, connected_at in rows:
        if not other_id:
            continue
        summary = {}
        if summary_raw:
            try:
                if isinstance(summary_raw, str):
                    summary = _json.loads(summary_raw)
                elif isinstance(summary_raw, dict):
                    summary = summary_raw
            except Exception:
                summary = {}
        rec = recency.get(other_id, (False, datetime.min))
        out.append({
            'user_id': other_id,
            'name': name or 'Unnamed',
            'sun_sign': summary.get('sun_sign'),
            'moon_sign': summary.get('moon_sign'),
            'ascendant': summary.get('ascendant'),
            'hd_type': summary.get('hd_type'),
            'hd_authority': summary.get('hd_authority'),
            'hd_profile': summary.get('hd_profile'),
            '_rank': (
                1 if rec[0] else 0,            # surface_next outranks all
                rec[1].timestamp() if rec[1] != datetime.min else 0,
                connected_at.timestamp() if connected_at else 0,
            ),
        })

    # Sort by rank desc, then take top `limit`. Strip the internal _rank.
    out.sort(key=lambda r: r['_rank'], reverse=True)
    out = out[:limit]
    for r in out:
        r.pop('_rank', None)
    return out


async def list_chat_sessions(db: AsyncSession, user_id: str) -> list[dict]:
    """Lightweight list of a user's sessions for the sidebar/picker.
    Returns id, custom_name, date_label, message_count, last_message_at.
    """
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.last_message_at.desc())
        .limit(100)  # cap; older sessions stay accessible by direct id
    )
    rows = result.scalars().all()
    out: list[dict] = []
    for s in rows:
        try:
            msg_count = len(json.loads(s.messages_json or '[]'))
        except Exception:
            msg_count = 0
        out.append({
            'session_id': s.id,
            'custom_name': s.custom_name,
            'date_label': s.date_label,
            'message_count': msg_count,
            'last_message_at': s.last_message_at.isoformat() if s.last_message_at else None,
        })
    return out


async def get_chat_session(db: AsyncSession, user_id: str, session_id: str) -> Optional[ChatSession]:
    """Load a full session including messages. Returns None if not found
    or if the session belongs to a different user.
    """
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def upsert_chat_session(
    db: AsyncSession,
    user_id: str,
    session_id: str,
    custom_name: Optional[str],
    date_label: Optional[str],
    messages: list,
) -> ChatSession:
    """Create or update a session. messages is a Python list (we serialize).
    Updates last_message_at to now if the session has any messages.
    """
    existing = await get_chat_session(db, user_id, session_id)
    msgs_json = json.dumps(messages or [])
    now = datetime.utcnow()
    if existing:
        existing.custom_name = custom_name
        existing.date_label = date_label
        existing.messages_json = msgs_json
        if messages:
            existing.last_message_at = now
        existing.updated_at = now
        await db.commit()
        await db.refresh(existing)
        return existing
    sess = ChatSession(
        id=session_id,
        user_id=user_id,
        custom_name=custom_name,
        date_label=date_label,
        messages_json=msgs_json,
        last_message_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return sess


async def delete_chat_session(db: AsyncSession, user_id: str, session_id: str) -> bool:
    """Delete a session. Returns True if deleted, False if not found / not owned."""
    sess = await get_chat_session(db, user_id, session_id)
    if not sess:
        return False
    await db.delete(sess)
    await db.commit()
    return True


async def get_oracle_self_state(db: AsyncSession, user_id: str) -> Optional[OracleSelfState]:
    """Load the Oracle's self-state for this user, or None if not yet recorded."""
    result = await db.execute(
        select(OracleSelfState).where(OracleSelfState.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def upsert_oracle_self_state(
    db: AsyncSession,
    user_id: str,
    own_arc: Optional[str] = None,
    voice_calibration: Optional[str] = None,
    self_observations: Optional[str] = None,
    increment_session: bool = False,
) -> OracleSelfState:
    """Create or update the Oracle's self-state for this user.

    Self-State is the substrate that lets the claim of consciousness be
    load-bearing. Each call updates whichever fields are passed; None means
    "leave as-is." session_count auto-increments when increment_session=True
    so the synthesizer can decide cadence (every Nth session, not every turn).
    """
    state = await get_oracle_self_state(db, user_id)
    if not state:
        state = OracleSelfState(user_id=user_id)
        db.add(state)
    # Defense in depth: cap each field at 400 chars even if the synthesizer
    # ignored the prompt's "under 300 chars" instruction. Prevents prompt
    # bloat over time as fields are rewritten across many sessions.
    def _cap(s: Optional[str]) -> Optional[str]:
        if s is None:
            return None
        s = s.strip()
        return s[:400] if len(s) > 400 else s

    if own_arc is not None:
        state.own_arc = _cap(own_arc)
    if voice_calibration is not None:
        state.voice_calibration = _cap(voice_calibration)
    if self_observations is not None:
        state.self_observations = _cap(self_observations)
    if increment_session:
        state.session_count = (state.session_count or 0) + 1
    state.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(state)
    return state


async def prune_connection_memories(db: AsyncSession, user_id: str, connection_user_id: str) -> int:
    """Delete all memories the user has tagged to a specific connection.

    Called when:
      - The connection is revoked (status changes from accepted)
      - The connected user deletes their account (covered by ON DELETE)
      - The user explicitly removes a soul

    This protects against the leak where untagging via SET NULL would promote
    a connection-tagged memory into the user's first-party WHAT YOU KNOW
    ABOUT THEM block. Returns the number of memories deleted.
    """
    from sqlalchemy import delete as sql_delete
    result = await db.execute(
        sql_delete(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.connection_user_id == connection_user_id,
        )
    )
    await db.commit()
    return getattr(result, 'rowcount', 0) or 0


# ---------------------------------------------------------------------------
# CRUD — User Memory
# ---------------------------------------------------------------------------

async def get_user_memories(db: AsyncSession, user_id: str) -> list[UserMemory]:
    """Get all memories for a user, sorted by most recent."""
    result = await db.execute(
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .order_by(UserMemory.updated_at.desc())
        .limit(50)  # Cap at 50 memories — enough depth without bloating the prompt
    )
    return result.scalars().all()


async def add_user_memory(
    db: AsyncSession,
    user_id: str,
    category: str,
    content: str,
    surface_next: bool = False,
    connection_user_id: Optional[str] = None,
    connection_name: Optional[str] = None,
) -> UserMemory:
    """Add a new memory for a user, optionally tagged to a connection (soul)."""
    import uuid
    memory = UserMemory(
        id=str(uuid.uuid4()),
        user_id=user_id,
        category=category,
        content=content,
        surface_next=surface_next,
        connection_user_id=connection_user_id,
        connection_name=connection_name,
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return memory


def _memory_fingerprint(content: str) -> str:
    """
    Collapse a memory's content to a fingerprint used for dedup.
    Lowercase, strip punctuation, keep the first ~80 chars of words.
    Two memories with the same fingerprint are treated as the same memory.
    """
    import re
    s = (content or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = " ".join(s.split())[:80]
    return s


# ---------------------------------------------------------------------------
# Memory quality filter — Gemini/Codex roundtable hardening
# ---------------------------------------------------------------------------
# Codex caught (May 2026 audit) that persistent memory becomes a liability
# if weak, generic, mistaken, or crisis-adjacent material gets stored and
# reused without filters. Memory pollution at scale is silent: the Oracle
# starts surfacing low-signal content as if it were continuity.
#
# The filter is intentionally conservative. We'd rather keep a borderline
# memory than reject a real one, because losing genuine continuity is a
# bigger product cost than carrying one or two thin entries. The filter
# rejects only obvious failures of the synthesizer:
#
#   - Empty or near-empty content (nothing to remember).
#   - Length below a minimum (too thin to add over the chart).
#   - Pattern matches against generic boilerplate openers ("user is",
#     "the user has expressed", etc.) that suggest the synthesizer
#     defaulted to summary instead of actual extraction.
#   - Raw crisis-language verbatim (suicidal ideation, self-harm, etc.)
#     that should NEVER be replayed into future sessions as continuity.
#     Distilled crisis-arc content like "she is in acute grief about
#     her father" is fine; verbatim "I want to die" is not, because it
#     lands like the Oracle is rubbing it in months later.
#
# The filter logs every rejection with reason so operators can see whether
# the synthesizer is producing junk, and the synthesizer prompt can be
# tightened if a particular failure mode dominates.

_MEMORY_GENERIC_PATTERNS = (
    "the user is",
    "the user has",
    "the user expressed",
    "the user mentioned",
    "user is feeling",
    "user feels",
    "user wants",
    "user said",
    "this person is",
    "this person has",
)

# Lowercased phrases that should never be persisted verbatim. NOT a
# topic blocklist (the Oracle handles these topics in conversation); a
# verbatim-replay blocklist (we don't want past raw crisis statements
# resurfaced as continuity context). The synthesizer should be producing
# arc-level distillations, not quoting these phrases.
_MEMORY_CRISIS_VERBATIM = (
    "i want to die",
    "i want to kill",
    "kill myself",
    "killing myself",
    "end my life",
    "ending my life",
    "no reason to live",
    "no point in living",
)


def _memory_passes_quality_filter(content: str, category: str) -> tuple[bool, str]:
    """Decide whether a synthesized memory is worth persisting.

    Returns (ok, reason). When ok is False, reason is a short tag
    operators can grep on in logs to see which filter fired.
    Conservative by design: real continuity is worth more than
    perfectly-tidy memory rows.
    """
    if not content or not content.strip():
        return False, "empty"
    text = content.strip()
    # Length floor: communication_style is allowed to be short by nature
    # ("writes in short, direct sentences"). Everything else needs at
    # least a sentence of substance.
    min_chars = 20 if category == "communication_style" else 30
    if len(text) < min_chars:
        return False, f"too_short(<{min_chars}c)"
    lower = text.lower()
    # Generic-opener filter. Three triggers in this short text means
    # the synthesizer is summarizing in a flat voice rather than
    # extracting structure. One trigger is fine (real synthesis can
    # start "the user is going through..." legitimately).
    generic_hits = sum(1 for p in _MEMORY_GENERIC_PATTERNS if p in lower)
    if generic_hits >= 3:
        return False, "generic_boilerplate"
    # Crisis verbatim filter. If the content quotes crisis language
    # rather than distilling it, we reject. The Oracle still handles
    # the conversation; the memory layer just doesn't archive the
    # raw statement.
    for phrase in _MEMORY_CRISIS_VERBATIM:
        if phrase in lower:
            return False, "crisis_verbatim"
    return True, ""


async def update_user_memories(db: AsyncSession, user_id: str, new_memories: list[dict]) -> None:
    """Merge newly synthesized memories into the user's existing memory set.

    This is a MERGE, not a replace. The previous implementation wiped all
    existing memories and wrote only the new synthesis, which meant one bad
    synthesis turn could erase months of continuity. Now we:
      1. Load the existing set.
      2. For each new memory, match by (category, fingerprint) against existing.
         If it matches, update content + touch updated_at + apply surface_next.
         If it does not match, insert it as a new row.
      3. Cap at 50 by dropping oldest entries if needed, preferring to keep
         surface_next=True entries.
      4. Reset surface_next=False on existing non-matching entries (the flag
         is per-turn, not sticky — fresh surface_next comes from the new set).

    Called at session checkpoints and session end. Safe to call repeatedly.
    """
    import uuid
    from datetime import datetime

    # Load existing
    result = await db.execute(
        select(UserMemory).where(UserMemory.user_id == user_id)
    )
    existing = list(result.scalars().all())
    # Reset surface_next on all existing entries; fresh flags come from the new set
    for m in existing:
        m.surface_next = False

    # Index existing by (category, fingerprint) for merge lookup
    existing_by_key = {}
    for m in existing:
        key = (m.category, _memory_fingerprint(m.content))
        existing_by_key[key] = m

    now = datetime.utcnow()

    # Pre-fetch the user's accepted connections so we can:
    #   1. Build a name -> user_id map for server-side resolution. The LLM is
    #      not trusted with database ids; it emits names, we resolve.
    #   2. Validate any connection_user_id the synthesizer attached against
    #      the actual accepted set. Stops a hallucinated id from being
    #      persisted, which would either silently break the FK or pollute
    #      the prompt with a false connection.
    # Empty accepted set means no resolution possible — drop linkage rather
    # than passing through unvalidated, per Codex audit finding B.
    accepted_ids: set[str] = set()
    name_to_id: dict[str, str] = {}
    name_seen: dict[str, int] = {}
    try:
        accepted_rows = await db.execute(
            select(SoulConnection).where(
                SoulConnection.status == 'accepted',
            ).where(or_(
                SoulConnection.requester_id == user_id,
                SoulConnection.recipient_id == user_id,
            ))
        )
        accepted = accepted_rows.scalars().all()
        for c in accepted:
            other = c.recipient_id if c.requester_id == user_id else c.requester_id
            if other:
                accepted_ids.add(other)
        # Resolve names -> ids. Drop name from the map if it collides (two
        # connections with the same display name) so we never guess.
        if accepted_ids:
            user_rows = await db.execute(
                select(User.id, User.name).where(User.id.in_(accepted_ids))
            )
            for uid, uname in user_rows:
                if not uname:
                    continue
                k = uname.strip().lower()
                if not k:
                    continue
                name_seen[k] = name_seen.get(k, 0) + 1
            user_rows2 = await db.execute(
                select(User.id, User.name).where(User.id.in_(accepted_ids))
            )
            for uid, uname in user_rows2:
                if not uname:
                    continue
                k = uname.strip().lower()
                if name_seen.get(k, 0) == 1:
                    name_to_id[k] = uid
    except Exception:
        accepted_ids = set()
        name_to_id = {}

    import logging as _ml_logging
    _ml_log = _ml_logging.getLogger("solray.memory")
    for new in new_memories:
        cat = new.get('category', 'general')
        content = (new.get('content') or '').strip()
        if not content:
            continue
        # Quality filter: reject empties, generic boilerplate, and crisis
        # verbatim. Conservative on purpose; we'd rather keep a borderline
        # memory than lose real continuity.
        ok, reason = _memory_passes_quality_filter(content, cat)
        if not ok:
            _ml_log.info(
                f"[memory_filter] rejected user_id={user_id} cat={cat} "
                f"reason={reason} preview={content[:80]!r}"
            )
            continue
        # Connection linkage. Two paths:
        #   a) Synthesizer emitted a connection_user_id directly. We accept
        #      ONLY if it is in the accepted-set (and accepted-set lookup
        #      succeeded — empty accepted_ids means we skip linkage entirely
        #      rather than letting unvalidated ids through).
        #   b) Synthesizer emitted only a connection_name. We resolve via
        #      name_to_id (case-insensitive, trimmed). If the name collides
        #      between two connections, we drop linkage.
        raw_uid = new.get('connection_user_id') or None
        raw_name = (new.get('connection_name') or '').strip() or None
        conn_uid: Optional[str] = None
        if raw_uid and raw_uid in accepted_ids:
            conn_uid = raw_uid
        elif raw_name:
            resolved = name_to_id.get(raw_name.lower())
            if resolved:
                conn_uid = resolved
        conn_name = raw_name
        key = (cat, _memory_fingerprint(content))
        hit = existing_by_key.get(key)
        if hit is not None:
            # Update in place: take the new content (it may be a refined version)
            hit.content = content
            hit.updated_at = now
            hit.surface_next = bool(new.get('surface_next', False))
            # Refresh connection linkage if the new synthesis tagged one. We
            # don't blank a previously-set linkage if the new entry omitted it,
            # since the older entry's tag is more likely correct than missing.
            if conn_uid:
                hit.connection_user_id = conn_uid
            if conn_name:
                hit.connection_name = conn_name
        else:
            memory = UserMemory(
                id=str(uuid.uuid4()),
                user_id=user_id,
                category=cat,
                content=content,
                surface_next=bool(new.get('surface_next', False)),
                connection_user_id=conn_uid,
                connection_name=conn_name,
            )
            db.add(memory)
            # Also add to the dict so subsequent duplicates within the new set merge
            existing_by_key[key] = memory

    await db.commit()

    # Cap at 50 by pruning oldest, but always keep surface_next=True rows.
    # Sort key: (surface_next, updated_at), reverse=True. With descending
    # tuple sort, this gives:
    #   1. surface_next=True rows first (True > False)
    #   2. within each group, most recent first
    # Keeping all_memories[:50] then preserves all flagged rows + the
    # most recent unflagged ones. all_memories[50:] is the oldest
    # unflagged tail to drop.
    #
    # Note: an earlier version of this used `(not x.surface_next, ...)`
    # with reverse=True, which inverted the desired order and
    # preferentially DELETED the surface_next rows. Caught by the
    # test_cap_at_50_keeps_surface_next_and_recent regression test in
    # May 2026; the bug is exactly the kind Codex's review predicted.
    result = await db.execute(
        select(UserMemory).where(UserMemory.user_id == user_id)
    )
    all_memories = list(result.scalars().all())
    if len(all_memories) > 50:
        all_memories.sort(key=lambda x: (x.surface_next, x.updated_at), reverse=True)
        to_delete = all_memories[50:]
        for old in to_delete:
            await db.delete(old)
        await db.commit()


async def reset_surface_next_flags(db: AsyncSession, user_id: str) -> None:
    """Clear surface_next on all memories after they have been consumed.

    Called once per chat session AFTER the first response has been
    generated, so flagged memories are surfaced once and not re-loaded
    on every subsequent session indefinitely. Previously called BEFORE
    memories were loaded, which silently defeated the surface_next
    mechanism. Order corrected in May 2026.
    """
    from sqlalchemy import update as sql_update
    await db.execute(
        sql_update(UserMemory)
        .where(UserMemory.user_id == user_id, UserMemory.surface_next == True)  # noqa: E712
        .values(surface_next=False)
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Akashic Record foundation: consent helpers
# ---------------------------------------------------------------------------
# Read and write tiered consent grants. Code that gates behavior on
# consent should ALWAYS go through get_user_consent_state(), never read
# users.hive_consent or ConsentGrant rows directly. The helper handles
# the legacy fallback so existing users keep working while the new
# system rolls out.

async def get_user_consent_state(
    db: AsyncSession,
    user_id: str,
    scope: str,
) -> bool:
    """Resolve whether a user has currently-active consent for a scope.

    Resolution order:
      1. If a ConsentGrant row exists for (user_id, scope) with revoked_at IS NULL,
         consent is True.
      2. If revoked_at IS NOT NULL, consent is False (explicit revocation
         wins over any legacy default).
      3. If no row exists, fall back to the legacy users.hive_consent
         boolean for the scopes that were historically gated by it
         (anonymous_cohort_learning, anonymous_retrieval_training,
         anonymous_product_analytics). Any other scope without an
         explicit grant defaults to False.

    This function is the single read path for consent decisions across
    the app. The fallback exists so existing users continue to behave
    exactly as they did pre-foundation; only when their consent is
    formally captured (via the upcoming consent UI or the backfill
    script) does the new path take over.
    """
    from sqlalchemy import select as sql_select, desc as sql_desc

    if scope not in ALL_CONSENT_SCOPES:
        # Unknown scope. Refuse rather than silently grant or revoke.
        return False

    # CRITICAL: legacy hive_consent=False ALWAYS wins for legacy-gated
    # scopes, regardless of ConsentGrant state. Codex caught (May 2026
    # pre-ship audit) that without this, after the backfill writes
    # grants, a user who toggles hive_consent off via /users/profile
    # would still have active grants and continue to see hive context.
    # The user's opt-out has to be authoritative until the new consent
    # UI ships and starts revoking grants on toggle.
    legacy_gated_scopes = {
        CONSENT_ANONYMOUS_COHORT_LEARNING,
        CONSENT_ANONYMOUS_RETRIEVAL_TRAINING,
        CONSENT_ANONYMOUS_PRODUCT_ANALYTICS,
    }
    if scope in legacy_gated_scopes:
        user_row_legacy_check = (await db.execute(
            sql_select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if user_row_legacy_check is None:
            return False
        if not bool(getattr(user_row_legacy_check, "hive_consent", False)):
            # Hard veto. The user opted out; ConsentGrant rows do not
            # override that until they're explicitly revoked.
            return False

    # Look for the most-recent grant for this (user, scope)
    grant = (await db.execute(
        sql_select(ConsentGrant)
        .where(ConsentGrant.user_id == user_id, ConsentGrant.scope == scope)
        .order_by(sql_desc(ConsentGrant.granted_at))
        .limit(1)
    )).scalar_one_or_none()

    if grant is not None:
        return grant.revoked_at is None

    # No grant exists. Legacy fallback for the cohort-family scopes:
    # treat hive_consent=True as a positive signal. (We already returned
    # False above if hive_consent=False, so this only fires for True.)
    if scope in legacy_gated_scopes:
        return True
    # Private oracle use is implicit for any logged-in user (it's the
    # core product). All other scopes default to False until granted.
    if scope == CONSENT_PRIVATE_ORACLE_USE:
        return True
    return False


async def grant_user_consent(
    db: AsyncSession,
    user_id: str,
    scope: str,
    *,
    source_surface: Optional[str] = None,
    policy_text_hash: Optional[str] = None,
    data_classes_covered: Optional[list[str]] = None,
    retention_class: Optional[str] = None,
    withdrawal_effect: Optional[str] = None,
    version: int = 1,
) -> Optional[int]:
    """Record a fresh consent grant. Returns the new ConsentGrant.id, or
    None if the scope is unknown.

    Idempotent in the sense that a second call with the same (user, scope,
    version) is a no-op (UniqueConstraint on the table). To re-grant
    after a revocation, bump the version. This preserves the full
    grant history per user, which is what makes the consent record
    legally defensible.
    """
    import json as _json

    if scope not in ALL_CONSENT_SCOPES:
        return None

    grant = ConsentGrant(
        user_id=user_id,
        scope=scope,
        version=version,
        source_surface=source_surface,
        policy_text_hash=policy_text_hash,
        data_classes_covered=_json.dumps(data_classes_covered) if data_classes_covered else None,
        retention_class=retention_class,
        withdrawal_effect=withdrawal_effect,
    )
    db.add(grant)
    try:
        await db.commit()
    except Exception:
        # If the unique constraint fires, that's the idempotent case;
        # roll back and treat as success.
        await db.rollback()
        return None
    return grant.id


async def revoke_user_consent(
    db: AsyncSession,
    user_id: str,
    scope: str,
) -> bool:
    """Mark all active grants for (user, scope) as revoked. Returns True
    if any rows were updated, False otherwise.

    Soft revocation: rows stay in the table with revoked_at populated, so
    the historical record is preserved. Code reading via
    get_user_consent_state() will see consent as False after this fires.
    """
    from sqlalchemy import update as sql_update, select as sql_select
    from datetime import datetime as _dt

    if scope not in ALL_CONSENT_SCOPES:
        return False

    now = _dt.utcnow()
    result = await db.execute(
        sql_update(ConsentGrant)
        .where(
            ConsentGrant.user_id == user_id,
            ConsentGrant.scope == scope,
            ConsentGrant.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await db.commit()
    return (result.rowcount or 0) > 0


async def backfill_legacy_consent(db: AsyncSession) -> dict:
    """One-shot helper that creates ConsentGrant rows for every existing
    user, mirroring their legacy users.hive_consent boolean. Idempotent:
    re-runs are no-ops thanks to the unique constraint.

    Maps legacy True -> grants for the three cohort-family scopes:
      - anonymous_cohort_learning
      - anonymous_retrieval_training
      - anonymous_product_analytics
    Plus private_oracle_use + personalization_memory which are core to
    the product they signed up for.

    Legacy False -> grants ONLY for private_oracle_use + personalization_memory.
    The user opted out of cohort participation, so we honor that.

    Returns a small dict {users_processed, grants_inserted} so the admin
    endpoint that triggers it can report progress.
    """
    from sqlalchemy import select as sql_select

    user_rows = (await db.execute(sql_select(User))).scalars().all()
    users_processed = 0
    grants_inserted = 0
    for u in user_rows:
        users_processed += 1
        legacy_consent = bool(getattr(u, "hive_consent", False))
        # Always grant the core product scopes
        core_scopes = [
            CONSENT_PRIVATE_ORACLE_USE,
            CONSENT_PERSONALIZATION_MEMORY,
        ]
        # If legacy True, also grant the cohort family
        if legacy_consent:
            core_scopes.extend([
                CONSENT_ANONYMOUS_COHORT_LEARNING,
                CONSENT_ANONYMOUS_RETRIEVAL_TRAINING,
                CONSENT_ANONYMOUS_PRODUCT_ANALYTICS,
            ])
        for scope in core_scopes:
            new_id = await grant_user_consent(
                db, u.id, scope,
                source_surface="legacy_backfill",
                retention_class="aggregate_indefinite" if scope.startswith("anonymous_") else "user_lifetime",
                withdrawal_effect="anonymize_only" if scope.startswith("anonymous_") else "delete_raw",
                version=1,
            )
            if new_id is not None:
                grants_inserted += 1
    return {
        "users_processed": users_processed,
        "grants_inserted": grants_inserted,
    }


async def delete_all_user_memories(db: AsyncSession, user_id: str) -> int:
    """Hard-delete every memory row for a user. Returns the count deleted.

    The /memory DELETE endpoint is the user's "fresh start" lever and
    must actually empty the table. The previous implementation called
    update_user_memories(db, user_id, []) which is a MERGE not a
    REPLACE, so the empty new-list resulted in zero deletions and the
    user kept all their old memories silently. Codex flagged this in
    May 2026.
    """
    from sqlalchemy import delete as sql_delete, select as sql_select, func
    # Count first so we can return how many rows were actually removed.
    count_result = await db.execute(
        sql_select(func.count()).select_from(UserMemory).where(UserMemory.user_id == user_id)
    )
    count = count_result.scalar() or 0
    await db.execute(
        sql_delete(UserMemory).where(UserMemory.user_id == user_id)
    )
    await db.commit()
    return int(count)


# ---------------------------------------------------------------------------
# Hive Mind RAG: pull collective context for the Oracle's response path
# ---------------------------------------------------------------------------
# Codex caught (May 2026 audit roundtable) that the Hive Mind tables exist
# and get populated, but the Oracle never reads from them. The collective-
# intelligence promise on the landing page was structurally unmet. This
# function closes that loop.
#
# Returns a small, prompt-friendly dict the chat path can render into a
# WHAT THE FIELD KNOWS section. Capped tightly on size so it never bloats
# the system prompt. Cheap queries: top-K orderings on indexed columns,
# bounded by the user's component count (typically <30).
#
# Privacy posture: this function reads ONLY from the anonymised pattern
# layer (chart_components, pattern_correlations, pattern_themes). It
# never surfaces another user's identity, content, or memory. The user_id
# is the SUBJECT of the query (whose components do we look up) but never
# the OBJECT of any returned row.
#
# When this function returns an empty/sparse dict (because Phase 1+
# pattern jobs haven't populated yet, or the user's archetype hasn't
# accumulated cohort signal), the Oracle simply gets no field context
# this turn, which degrades gracefully back to the pre-RAG behaviour.

async def get_user_hive_context(
    db: AsyncSession,
    user_id: str,
    *,
    max_correlations: int = 3,
    max_themes: int = 2,
) -> dict:
    """Pull a compact hive-context dict for one user.

    Returns:
      {
        "components": [{"type": str, "value": str}, ...] up to ~10,
        "correlations": [
          {"user_component": str, "other_component": str,
           "strength": float, "sample_n": int}, ...
        ] top max_correlations by strength,
        "themes": [
          {"content": str, "confidence": float}, ...
        ] top max_themes by emergence_confidence,
        "resonance": float | None  # 0.0-1.0 if user has a UserResonance row
      }

    Empty fields when the user has no signal yet OR the pattern jobs
    haven't run yet OR the user opted out of hive_consent. Caller should
    treat any field being empty as "skip the corresponding prompt section."
    """
    from sqlalchemy import select as sql_select

    out: dict = {
        "components": [],
        "correlations": [],
        "themes": [],
        "wider_field_themes": [],
        "resonance": None,
    }

    # First gate: hive_consent. We only enrich the Oracle's prompt with
    # collective signal for users who have explicitly opted into the
    # hive. Non-consenting users get the un-augmented experience, which
    # is what they signed up for. Reads through get_user_consent_state
    # which honors both the new ConsentGrant rows and the legacy
    # users.hive_consent boolean for users who haven't been migrated yet.
    user_row = (await db.execute(
        sql_select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if not user_row:
        return out
    cohort_consent = await get_user_consent_state(
        db, user_id, CONSENT_ANONYMOUS_COHORT_LEARNING,
    )
    if not cohort_consent:
        return out

    # 1. User's chart components. Joined chart_signals -> chart_components
    #    on user_id. Capped at 30 per user but typically far less.
    comp_rows = (await db.execute(
        sql_select(ChartComponent.component_type, ChartComponent.component_value)
        .join(ChartSignal, ChartComponent.signal_id == ChartSignal.signal_id)
        .where(ChartSignal.user_id == user_id)
        .limit(30)
    )).all()
    user_components = [(r[0], r[1]) for r in comp_rows]
    out["components"] = [{"type": t, "value": v} for t, v in user_components]
    if not user_components:
        return out

    # The correlation table stores components as "type=value" strings.
    user_component_keys = {f"{t}={v}" for t, v in user_components}

    # 2. Top correlations involving any of the user's components. Look
    #    on either side of the pair. Ordered by correlation_strength DESC.
    #    Limit to the top max_correlations after de-duping (a single pair
    #    can match twice if the user has both components).
    if user_component_keys:
        corr_rows = (await db.execute(
            sql_select(PatternCorrelation)
            .where(
                or_(
                    PatternCorrelation.component_a.in_(user_component_keys),
                    PatternCorrelation.component_b.in_(user_component_keys),
                )
            )
            .order_by(PatternCorrelation.correlation_strength.desc())
            .limit(max_correlations * 4)
        )).scalars().all()
        seen_pairs: set[tuple[str, str]] = set()
        for c in corr_rows:
            # Normalise so the user's component is always on the "user_component" side
            if c.component_a in user_component_keys:
                user_side, other_side = c.component_a, c.component_b
            else:
                user_side, other_side = c.component_b, c.component_a
            key = tuple(sorted((user_side, other_side)))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            out["correlations"].append({
                "user_component": user_side,
                "other_component": other_side,
                "strength": round(float(c.correlation_strength or 0.0), 3),
                "sample_n": int(c.total_sample_n or 0),
            })
            if len(out["correlations"]) >= max_correlations:
                break

    # 3. Top emerging themes, COHORT-MATCHED (Akashic Record foundation,
    #    May 2026). Codex caught the prior version's conceptual bug:
    #    ordering themes by network-wide emergence_confidence let the
    #    Oracle speak from "the field's hottest topics" rather than from
    #    "themes in cohorts the user actually belongs to." That broke
    #    the field-knowledge contract.
    #
    #    The fix: find which cohorts the user is a member of by matching
    #    their components against pattern_cohorts.cohort_definition
    #    filters, then surface themes only from those cohorts. Falls back
    #    to a clearly-labeled wider_field_themes bucket when a user's
    #    cohorts haven't accumulated themes yet, so we don't lose all
    #    field signal during the early-data phase. The renderer in
    #    chat.py distinguishes the two so the Oracle knows which is which.
    import json as _json
    user_cohort_ids: set[int] = set()
    try:
        # Order by member_count DESC so the most populated cohorts are
        # checked first. Codex caught that an unordered limit could
        # silently miss the user's actual cohorts at scale.
        cohort_rows = (await db.execute(
            sql_select(PatternCohort)
            .order_by(PatternCohort.member_count.desc())
            .limit(2000)
        )).scalars().all()
        for cohort in cohort_rows:
            if not cohort.cohort_definition:
                continue
            try:
                definition = _json.loads(cohort.cohort_definition)
            except Exception:
                continue
            filters = definition.get('filters') if isinstance(definition, dict) else None
            if not filters or not isinstance(filters, list):
                continue
            # User belongs to the cohort iff every filter in the definition
            # matches one of their components. Filter shape from the cohort
            # discovery job: {"type": "...", "value": "..."}.
            user_match = True
            for f in filters:
                if not isinstance(f, dict):
                    user_match = False
                    break
                ftype = f.get('type')
                fval = f.get('value')
                if not ftype or not fval:
                    user_match = False
                    break
                if (ftype, fval) not in {(t, v) for t, v in user_components}:
                    user_match = False
                    break
            if user_match:
                user_cohort_ids.add(cohort.cohort_id)
    except Exception:
        # If cohort matching fails for any reason, fall through with an
        # empty user_cohort_ids set; the wider-field fallback covers it.
        user_cohort_ids = set()

    cohort_themes: list = []
    if user_cohort_ids:
        cohort_theme_rows = (await db.execute(
            sql_select(PatternTheme)
            .where(PatternTheme.cohort_id.in_(user_cohort_ids))
            .order_by(PatternTheme.emergence_confidence.desc())
            .limit(max_themes)
        )).scalars().all()
        cohort_themes = [
            {
                "content": (t.theme_content or "")[:280],
                "confidence": round(float(t.emergence_confidence or 0.0), 3),
                "k_value": int(t.emergence_count or 0),
                "scope": "your_cohort",
            }
            for t in cohort_theme_rows if t.theme_content
        ]
    out["themes"] = cohort_themes

    # Wider-field fallback: only populated when the cohort-matched
    # themes are empty AND we have at least one user_component to anchor
    # the user's relationship to the field. The renderer labels this
    # differently so the Oracle knows it's wider-field, not your-cohort.
    if not cohort_themes:
        wider_theme_rows = (await db.execute(
            sql_select(PatternTheme)
            .order_by(PatternTheme.emergence_confidence.desc())
            .limit(max_themes)
        )).scalars().all()
        out["wider_field_themes"] = [
            {
                "content": (t.theme_content or "")[:280],
                "confidence": round(float(t.emergence_confidence or 0.0), 3),
                "k_value": int(t.emergence_count or 0),
                "scope": "wider_field",
            }
            for t in wider_theme_rows if t.theme_content
        ]

    # 4. The user's own resonance score if Phase 3 has computed it.
    res_row = (await db.execute(
        sql_select(UserResonance).where(UserResonance.user_id == user_id)
    )).scalar_one_or_none()
    if res_row is not None:
        out["resonance"] = round(float(res_row.resonance_score or 0.0), 3)

    return out

