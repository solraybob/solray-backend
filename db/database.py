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
    UniqueConstraint, CheckConstraint, select, update, delete, case, or_
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
    email_verified   = Column(Boolean,     nullable=False, default=False)
    verification_token = Column(String(64), nullable=True)   # random token for email verify link
    password_reset_token   = Column(String(64), nullable=True)   # random token for password reset link
    password_reset_expires = Column(DateTime,   nullable=True)   # token expiry (typically NOW + 1h)
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
    return bp


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

    for new in new_memories:
        cat = new.get('category', 'general')
        content = (new.get('content') or '').strip()
        if not content:
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

