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
    Column, String, Float, Date, DateTime, Text, ForeignKey, Boolean,
    UniqueConstraint, CheckConstraint, select, update, delete
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
    email_verified   = Column(Boolean,     nullable=False, default=False)
    verification_token = Column(String(64), nullable=True)   # random token for email verify link
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
    category     = Column(String(50), nullable=False)   # life_event, theme, insight, preference, communication_style, etc.
    content      = Column(Text, nullable=False)          # The memory itself
    surface_next = Column(Boolean, nullable=False, default=False)  # actively reference in next session
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


async def add_user_memory(db: AsyncSession, user_id: str, category: str, content: str, surface_next: bool = False) -> UserMemory:
    """Add a new memory for a user."""
    import uuid
    memory = UserMemory(
        id=str(uuid.uuid4()),
        user_id=user_id,
        category=category,
        content=content,
        surface_next=surface_next,
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

    for new in new_memories:
        cat = new.get('category', 'general')
        content = (new.get('content') or '').strip()
        if not content:
            continue
        key = (cat, _memory_fingerprint(content))
        hit = existing_by_key.get(key)
        if hit is not None:
            # Update in place: take the new content (it may be a refined version)
            hit.content = content
            hit.updated_at = now
            hit.surface_next = bool(new.get('surface_next', False))
        else:
            memory = UserMemory(
                id=str(uuid.uuid4()),
                user_id=user_id,
                category=cat,
                content=content,
                surface_next=bool(new.get('surface_next', False)),
            )
            db.add(memory)
            # Also add to the dict so subsequent duplicates within the new set merge
            existing_by_key[key] = memory

    await db.commit()

    # Cap at 50 by pruning oldest, but always keep surface_next=True rows.
    result = await db.execute(
        select(UserMemory).where(UserMemory.user_id == user_id)
    )
    all_memories = list(result.scalars().all())
    if len(all_memories) > 50:
        # Keep surface_next=True first, then most recent. Drop the remainder.
        all_memories.sort(key=lambda x: (not x.surface_next, x.updated_at), reverse=True)
        to_delete = all_memories[50:]
        for old in to_delete:
            await db.delete(old)
        await db.commit()


async def reset_surface_next_flags(db: AsyncSession, user_id: str) -> None:
    """Clear surface_next on all memories after a session starts.

    Called at the beginning of a chat session so flagged memories are consumed
    once and not re-surfaced every session indefinitely.
    """
    from sqlalchemy import update as sql_update
    await db.execute(
        sql_update(UserMemory)
        .where(UserMemory.user_id == user_id, UserMemory.surface_next == True)  # noqa: E712
        .values(surface_next=False)
    )
    await db.commit()

