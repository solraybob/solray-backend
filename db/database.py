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

# If DATABASE_URL is not set, default to local SQLite for development.
# For production, DATABASE_URL must be explicitly set as an environment variable.
_RAW_DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///./solray.db')

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
    """
    __tablename__ = 'user_memory'

    id         = Column(String(36), primary_key=True)
    user_id    = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    category   = Column(String(50), nullable=False)   # e.g. 'life_event', 'theme', 'insight', 'preference'
    content    = Column(Text, nullable=False)           # The memory itself
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


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
        .limit(20)  # Cap at 20 memories to keep system prompt manageable
    )
    return result.scalars().all()


async def add_user_memory(db: AsyncSession, user_id: str, category: str, content: str) -> UserMemory:
    """Add a new memory for a user."""
    import uuid
    memory = UserMemory(
        id=str(uuid.uuid4()),
        user_id=user_id,
        category=category,
        content=content,
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return memory


async def update_user_memories(db: AsyncSession, user_id: str, memories: list[dict]) -> None:
    """Replace all memories for a user with a new set. Called after AI synthesizes a chat session."""
    # Delete old memories
    await db.execute(delete(UserMemory).where(UserMemory.user_id == user_id))
    # Add new ones
    import uuid
    for m in memories[:20]:  # Cap at 20
        memory = UserMemory(
            id=str(uuid.uuid4()),
            user_id=user_id,
            category=m.get('category', 'general'),
            content=m.get('content', ''),
        )
        db.add(memory)
    await db.commit()

