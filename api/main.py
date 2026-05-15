"""
api/main.py — Solray AI REST API

FastAPI backend exposing:
  POST   /users/register          — Register + generate blueprint
  POST   /users/login             — Email/password auth
  GET    /users/me                — Profile + blueprint
  GET    /forecast/today          — Daily forecast (cached, AI-generated)
  POST   /chat                    — Higher Self chat
  POST   /souls/invite            — Invite a soul connection
  POST   /souls/accept/{id}       — Accept a soul invite
  GET    /souls                   — List accepted soul connections
  GET    /souls/{id}/synergy      — Synergy reading for self + soul

Run with:
  uvicorn api.main:app --reload --port 8000
"""

import sys
import os
import uuid
import logging
from datetime import date, datetime
from typing import Optional, List

# Surface INFO/WARNING logs in the Railway console. Without this, Python's
# default WARNING-only root logger silently swallows diagnostics like the
# Teya SecurePay URL trace.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Background-task registry
# ---------------------------------------------------------------------------
# asyncio.create_task returns a task that the event loop only weakly
# references; if the caller doesn't keep a strong reference, the task can
# be garbage-collected mid-flight, leaving "Task was destroyed but it is
# pending" warnings and silently dropped audit rows. Codex audit (May
# 2026) caught this on the new audit-pipeline call sites. Pattern is the
# canonical Python advice: a module-level set holds strong refs, and each
# task removes itself when done.
import asyncio as _aio_bg
_BACKGROUND_TASKS: set[_aio_bg.Task] = set()

def _spawn_background(coro):
    """Schedule a fire-and-forget coroutine and retain a strong reference.

    Returns the created Task so callers can attach error callbacks if they
    want, but most callers should just let it run. The discard callback
    cleans the set as tasks complete so it doesn't grow unbounded.
    """
    task = _aio_bg.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task

# ---------------------------------------------------------------------------
# Sentry — Error Monitoring (graceful: no-op if SENTRY_DSN not set)
# ---------------------------------------------------------------------------
try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    _SENTRY_DSN = os.environ.get('SENTRY_DSN', '')
    if _SENTRY_DSN:
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            traces_sample_rate=0.1,
            environment=os.environ.get('ENVIRONMENT', 'production'),
            # Don't send PII by default
            send_default_pii=False,
        )
except ImportError:
    pass  # sentry-sdk not installed — monitoring disabled

from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from timezonefinder import TimezoneFinder
import pytz

# Add project root to path so engines.py is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api.auth import (
    hash_password, verify_password,
    create_access_token, get_current_user_id
)
from db.database import (
    get_db, init_db, AsyncSession,
    create_user, get_user_by_id, get_user_by_email,
    get_user_by_username, search_users,
    upsert_blueprint, get_blueprint,
    get_cached_forecast, cache_forecast,
    create_soul_invite, get_soul_connection,
    accept_soul_connection, decline_soul_connection,
    get_accepted_souls, get_pending_invites_for_user,
    get_user_memories, update_user_memories, add_user_memory,
    reset_surface_next_flags,
    User,
    MarketingEvent, IntegrationCredential, MarketingSignal,
)
import engines
from ai.forecast import generate_daily_forecast
from ai.chat import chat as higher_self_chat, group_chat as group_higher_self_chat
from energy_calculator import calculate_energy_scores
from lunar import get_upcoming_lunar_event

# Payments
from payments.subscription_manager import (
    get_subscription, start_trial, attach_card,
    convert_trial_to_active, cancel_subscription, has_premium_access,
)
from payments.teya_client import teya, TeyaError
from payments.feature_gate import require_premium
from email_service import generate_verification_token, send_verification_email

# ---------------------------------------------------------------------------
# App Setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title='Solray AI',
    description='Personal astrology + Human Design + Gene Keys blueprint API',
    version='1.0.0',
    docs_url='/docs',
    redoc_url='/redoc',
)

# Admin email list for authorization checks. Loaded from env so we can
# grant or revoke access without a code deploy. SOLRAY_ADMIN_EMAILS is a
# comma-separated list. Defaults below are the floor: even if env is
# unset or misconfigured, these two always have access.
_DEFAULT_ADMIN_EMAILS = {
    "kristjangilbert@gmail.com",
    "kristjangilbert@protonmail.com",
    "davidsnaerj@gmail.com",
}
_ADMIN_EMAILS_FROM_ENV = {
    e.strip().lower()
    for e in os.environ.get("SOLRAY_ADMIN_EMAILS", "").split(",")
    if e.strip()
}
ADMIN_EMAILS = {e.lower() for e in _DEFAULT_ADMIN_EMAILS} | _ADMIN_EMAILS_FROM_ENV

# CORS — restrict to known frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.solray.ai",
        "https://solray.ai",
        "http://localhost:3000",
        "http://localhost:3001",
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.on_event('startup')
async def startup():
    """Initialise DB tables on first start, kick off billing scheduler."""
    await init_db()

    # Start the API-usage logger queue + background writer. Codex audit
    # (May 2026) recommended in-process queue with batched inserts over
    # per-request background tasks. Drains queue every 10 items or 2s,
    # whichever first. Non-fatal if it fails.
    try:
        from ai.usage_logger import start_writer as _start_usage_writer
        await _start_usage_writer()
    except Exception as e:
        logger.warning(f"[startup] usage logger writer failed to start: {e}")

    # Model health check. Tonight (2026-05-12) the Oracle went down because a
    # ghost model ID was lurking in the advisor for weeks, then got copied to
    # the chat path. The try/except masked the failure. This probe pings every
    # production Anthropic model with a 5-token test message at boot. Logs
    # OK/FAIL for each. Does not fail startup; the resilience layer handles
    # per-call failures. The point is to surface the bad ID in logs before
    # traffic hits, not to crash the process.
    try:
        import asyncio as _asyncio
        from ai.chat import verify_models_at_startup
        # Run the (sync) probe in a worker thread so it does not block the
        # FastAPI event loop during startup. Sequential probe is ~2-3s total.
        results = await _asyncio.to_thread(verify_models_at_startup)
        bad = [m for m, status in results.items() if not str(status).startswith('ok') and m != '_error']
        if bad:
            logger.error(f"[startup-probe] {len(bad)} model(s) failed verification: {bad}")
        else:
            logger.info(f"[startup-probe] all {len(results)} models verified ok")
    except Exception as e:
        logger.warning(f"[startup-probe] verification raised: {e}")

    # One-time (idempotent) backfill: every existing user who was signed up
    # before subscriptions existed gets a fresh 5-day trial from today.
    try:
        from payments.billing_scheduler import backfill_trials_for_existing_users
        await backfill_trials_for_existing_users()
    except Exception as e:
        logger.warning("[startup] Trial backfill failed: %s", e)

    # Start the background billing loop (checks every 60 min)
    import asyncio
    from payments.billing_scheduler import billing_loop
    asyncio.create_task(billing_loop(interval_minutes=60))


# ---------------------------------------------------------------------------
# Admin Authorization Helper
# ---------------------------------------------------------------------------

async def require_admin(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> str:
    """
    Dependency for admin-only endpoints.
    Verifies that the authenticated user has admin privileges.
    Returns user_id if authorized, raises HTTPException 403 otherwise.
    """
    user = await get_user_by_id(db, user_id)
    if not user or (user.email or "").strip().lower() not in ADMIN_EMAILS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin access required'
        )
    return user_id


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    name:       str        = Field(..., example='Alice Sun')
    email:      EmailStr   = Field(..., example='alice@example.com')
    password:   str        = Field(..., min_length=8, example='s3cr3t!!')
    birth_date: str        = Field(..., example='1990-06-15', description='YYYY-MM-DD')
    birth_time: str        = Field(..., example='14:30',      description='HH:MM')
    birth_city: str        = Field(..., example='London')
    sex:        Optional[str] = Field(None, example='female', description="'male' or 'female'")
    tz_offset:  float      = Field(0.0, example=1.0, description='UTC offset at birth (e.g. 1.0 for BST)')
    username:   Optional[str] = Field(None, example='alicesun', description='Optional username (auto-generated if omitted)')
    hive_consent: Optional[bool] = Field(True, example=True, description='Consent to chart joining the anonymized collective. Defaults to True; set False to opt out at signup.')


class SoulBlueprintRequest(BaseModel):
    name:       Optional[str] = Field(None, example='Alice Sun')
    birth_date: str        = Field(..., example='1990-06-15', description='YYYY-MM-DD')
    birth_time: str        = Field(..., example='14:30',      description='HH:MM')
    birth_city: str        = Field(..., example='London')
    sex:        Optional[str] = Field(None, example='female', description="'male' or 'female'")


class LoginRequest(BaseModel):
    email:    EmailStr = Field(..., example='alice@example.com')
    password: str      = Field(..., example='s3cr3tpass')


class TokenResponse(BaseModel):
    token:   str
    user_id: str


class ChatMessage(BaseModel):
    role: str = Field(..., description='Either "user" or "assistant"')
    content: str = Field(..., description='Message content')


class SoulInviteRequest(BaseModel):
    identifier: str = Field(..., description='Username (@handle) or email of the person to invite')
    message: Optional[str] = Field(None, description='Optional message to include with the invite')


class InviteRequest(BaseModel):
    email: EmailStr = Field(..., description='Email of the person to invite (legacy, use SoulInviteRequest)')


class GroupChatRequest(BaseModel):
    message: str = Field(..., description='The message sent in the group chat')
    sender_username: str = Field(..., description='Username of whoever is sending this message')
    soul_connection_id: str = Field(..., description='Accepted soul connection ID')
    conversation_history: List[ChatMessage] = Field(default=[], description='Prior group conversation history')


class ChatRequest(BaseModel):
    message: str = Field(..., description='The user message to the Higher Self')
    conversation_history: List[ChatMessage] = Field(
        default=[], description='Prior conversation turns'
    )
    soul_blueprint: Optional[dict] = Field(
        default=None, description='Optional soul blueprint for compatibility readings'
    )


class UserProfile(BaseModel):
    id:          str
    email:       str
    name:        str
    birth_date:  str
    birth_time:  str
    birth_city:  Optional[str]
    birth_lat:   Optional[float]
    birth_lon:   Optional[float]
    created_at:  datetime


# ---------------------------------------------------------------------------
# Helper: auto-generate username from name
# ---------------------------------------------------------------------------

import re

def _generate_username_from_name(name: str) -> str:
    """Generate a URL-safe lowercase username from a display name."""
    base = name.lower()
    base = re.sub(r'[^a-z0-9]', '', base)
    base = base[:30] or 'user'
    return base


async def _find_unique_username(db, base: str) -> str:
    """Append a number suffix until the username is unique."""
    candidate = base
    counter = 1
    while True:
        existing = await get_user_by_username(db, candidate)
        if not existing:
            return candidate
        candidate = f"{base}{counter}"
        counter += 1


# ---------------------------------------------------------------------------
# Helper: get timezone offset from lat/lon + birth datetime
# ---------------------------------------------------------------------------

def get_tz_offset(lat: float, lon: float, birth_date: str, birth_time: str) -> float:
    """Get UTC offset in hours for a given location and datetime."""
    tf = TimezoneFinder()
    tz_name = tf.timezone_at(lat=lat, lng=lon)
    if not tz_name:
        return 0.0
    tz = pytz.timezone(tz_name)
    dt_str = f"{birth_date} {birth_time}"
    try:
        naive_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        localized = tz.localize(naive_dt)
        offset_seconds = localized.utcoffset().total_seconds()
        return offset_seconds / 3600
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Helper: build user profile dict
# ---------------------------------------------------------------------------

def _user_profile(user: User) -> dict:
    return {
        'id':            user.id,
        'email':         user.email,
        'username':      user.username,
        'name':          user.name,
        'birth_date':    user.birth_date,
        'birth_time':    user.birth_time,
        'birth_city':    user.birth_city,
        'birth_lat':     user.birth_lat,
        'birth_lon':     user.birth_lon,
        'sex':           getattr(user, 'sex', None),
        'profile_photo': getattr(user, 'profile_photo', None),
        'is_public':     bool(getattr(user, 'is_public', False)),
        'hive_consent':  bool(getattr(user, 'hive_consent', True)),
        'created_at':    user.created_at.isoformat() if user.created_at else None,
    }


# ---------------------------------------------------------------------------
# POST /users/register
# ---------------------------------------------------------------------------

@app.post(
    '/users/register',
    summary='Register a new user and generate their blueprint',
    status_code=201,
)
async def register(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a new user account, geocodes their birth city,
    runs all three engines (astrology + Human Design + Gene Keys),
    stores the full blueprint, and returns a JWT token.
    """
    # Check email not already taken
    existing = await get_user_by_email(db, req.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='An account with this email already exists',
        )

    # Resolve username
    if req.username:
        desired = re.sub(r'[^a-z0-9_]', '', req.username.lower())[:30] or _generate_username_from_name(req.name)
        username = await _find_unique_username(db, desired)
    else:
        base = _generate_username_from_name(req.name)
        username = await _find_unique_username(db, base)

    # Geocode the birth city (raises ValueError if not found)
    try:
        from astrology import geocode_city
        birth_lat, birth_lon = geocode_city(req.birth_city)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Normalize sex to 'male' | 'female' | None
    sex_clean: Optional[str] = None
    if req.sex:
        s = req.sex.strip().lower()
        if s in ('male', 'm'):
            sex_clean = 'male'
        elif s in ('female', 'f'):
            sex_clean = 'female'

    # Create user row with verification token
    user_id = str(uuid.uuid4())
    v_token = generate_verification_token()
    user = await create_user(db, {
        'id':            user_id,
        'email':         req.email,
        'username':      username,
        'name':          req.name,
        'password_hash': hash_password(req.password),
        'birth_date':    req.birth_date,
        'birth_time':    req.birth_time,
        'birth_city':    req.birth_city,
        'birth_lat':     birth_lat,
        'birth_lon':     birth_lon,
        'sex':           sex_clean,
        'email_verified': False,
        'verification_token': v_token,
        # Honor the consent choice from onboarding. None defaults to True
        # via the column default, matching how existing users were treated.
        'hive_consent':  True if req.hive_consent is None else bool(req.hive_consent),
    })

    # Auto-detect timezone from coordinates (ignores any client-supplied tz_offset)
    tz_offset = get_tz_offset(birth_lat, birth_lon, req.birth_date, req.birth_time)

    # Build the full blueprint (this is the expensive calculation)
    try:
        blueprint = engines.build_blueprint(
            birth_date=req.birth_date,
            birth_time=req.birth_time,
            birth_city=req.birth_city,
            birth_lat=birth_lat,
            birth_lon=birth_lon,
            tz_offset=tz_offset,
        )
    except Exception as e:
        # Blueprint calculation failed. The user row was already committed
        # by create_user — if we leave it there, the user's email is now
        # permanently taken with a half-formed account they can't log
        # into and can't re-register. Roll the user row back so they can
        # retry with the same email (or a corrected birth city).
        logger.exception(f"Blueprint calculation failed during registration for {req.email}")
        try:
            import sentry_sdk as _sentry
            _sentry.capture_exception(e)
        except Exception:
            logger.warning("Sentry not available for error reporting")
        try:
            from sqlalchemy import delete as _delete
            from db.database import User as _User
            await db.execute(_delete(_User).where(_User.id == user_id))
            await db.commit()
        except Exception as _cleanup_err:
            logger.exception(
                "Failed to roll back user row after blueprint failure for %s; "
                "manual cleanup required: %s", req.email, _cleanup_err,
            )
        raise HTTPException(
            status_code=500,
            detail=(
                'Could not build your chart from that birth data. Please '
                'check the city name and try again.'
            ),
        )

    # Store blueprint
    await upsert_blueprint(db, user_id, blueprint)

    # Send verification email (non-blocking: don't fail registration if email fails)
    try:
        await send_verification_email(req.email, req.name, v_token)
    except Exception as e:
        logger.warning("[register] Verification email failed for %s: %s", req.email, e)

    # Auto-start the 5-day free trial on registration
    try:
        await start_trial(db, user_id)
    except Exception as e:
        logger.warning("[register] Trial start failed for %s: %s", req.email, e)

    # Issue JWT
    token = create_access_token(user_id=user_id, email=req.email)

    return {
        'user_id':   user_id,
        'token':     token,
        'profile':   _user_profile(user),
        'blueprint': blueprint,
        'email_verified': False,
    }


# ---------------------------------------------------------------------------
# POST /users/login
# ---------------------------------------------------------------------------

@app.post('/users/login', summary='Login and get a JWT token')
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with email + password.
    Returns a JWT access token on success.
    """
    user = await get_user_by_email(db, req.email)
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid email or password',
        )

    token = create_access_token(user_id=user.id, email=user.email)
    return {
        "token": token,
        "user_id": user.id,
        "email_verified": bool(user.email_verified),
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
        }
    }


# ---------------------------------------------------------------------------
# Email Verification
# ---------------------------------------------------------------------------

@app.get('/users/verify-email', summary='Verify email address via token')
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Called when the user clicks the link in their verification email."""
    from sqlalchemy import update as sql_update
    from db.database import User as UserModel

    result = await db.execute(
        select(UserModel).where(UserModel.verification_token == token)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link.")

    if user.email_verified:
        return {"message": "Email already verified.", "email_verified": True}

    user.email_verified = True
    user.verification_token = None
    await db.commit()

    return {"message": "Email verified successfully.", "email_verified": True}


@app.post('/users/resend-verification', summary='Resend the verification email')
async def resend_verification(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Send a fresh verification email. Generates a new token each time."""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.email_verified:
        return {"message": "Email already verified.", "email_verified": True}

    # Generate a fresh token
    new_token = generate_verification_token()
    user.verification_token = new_token
    await db.commit()

    sent = await send_verification_email(user.email, user.name, new_token)
    if not sent:
        raise HTTPException(status_code=502, detail="Could not send verification email. Please try again.")

    return {"message": "Verification email sent."}


# ---------------------------------------------------------------------------
# GET /users/me
# ---------------------------------------------------------------------------

@app.get('/users/me', summary='Get current user profile + blueprint')
async def get_me(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the authenticated user's full profile and stored blueprint.
    Requires Bearer token.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')

    blueprint = await get_blueprint(db, user_id)

    # Inject numerology into blueprint if not already present
    if blueprint and user.birth_date and user.name:
        try:
            from numerology import calculate_numerology
            numerology = calculate_numerology(user.birth_date, user.name)
            blueprint['numerology'] = numerology
        except Exception:
            pass  # Numerology is non-critical; don't fail the request

    return {
        'profile':   _user_profile(user),
        'blueprint': blueprint,
        'email_verified': bool(user.email_verified),
    }


# ---------------------------------------------------------------------------
# POST /users/forgot-password — request a password reset link
# ---------------------------------------------------------------------------

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@app.post('/users/forgot-password', summary='Request a password reset email')
async def forgot_password(
    req: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate a reset token, store it on the user with an expiry, and
    send an email containing the reset link.

    Anti-enumeration: this endpoint ALWAYS returns the same success
    payload, whether the email exists or not. We never reveal which
    addresses have accounts. The downside is users who mistype their
    email get a soft "check your inbox" with no clue why no email
    arrived; the upside is attackers can't probe for valid emails.
    """
    from email_service import send_password_reset_email, generate_verification_token
    from datetime import datetime, timedelta

    user = await get_user_by_email(db, req.email.lower())
    if user:
        token = generate_verification_token()
        user.password_reset_token = token
        user.password_reset_expires = datetime.utcnow() + timedelta(hours=1)
        try:
            await db.commit()
        except Exception as e:
            logger.warning("[forgot_password] commit failed for %s: %s", req.email, e)
            await db.rollback()
            # Fall through — return ok anyway so we don't leak existence
        else:
            try:
                await send_password_reset_email(user.email, user.name or "there", token)
            except Exception as e:
                logger.warning("[forgot_password] email send failed for %s: %s", user.email, e)

    # Same shape regardless of whether the user existed.
    return {
        "ok": True,
        "message": "If that email is registered, a reset link is on the way."
    }


# ---------------------------------------------------------------------------
# POST /users/reset-password — consume a reset token, set new password
# ---------------------------------------------------------------------------

class ResetPasswordRequest(BaseModel):
    token:        str = Field(..., min_length=32, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=200)


@app.post('/users/reset-password', summary='Set a new password using a reset token')
async def reset_password(
    req: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Find the user by token, verify it hasn't expired, hash + store
    the new password, and invalidate the token. Returns a fresh JWT
    so the user is logged in immediately after reset (no re-login UX
    on top of a frustrating forgot-password flow).
    """
    from sqlalchemy import select as _select
    from datetime import datetime
    from db.database import User as _User

    # Look up by token only — no email needed in the request, the
    # token itself proves the user controls the inbox we sent it to.
    result = await db.execute(
        _select(_User).where(_User.password_reset_token == req.token)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used.")

    if not user.password_reset_expires or user.password_reset_expires < datetime.utcnow():
        # Clear the stale token so it can't be re-attempted.
        user.password_reset_token = None
        user.password_reset_expires = None
        await db.commit()
        raise HTTPException(status_code=400, detail="This reset link has expired. Request a new one from the login page.")

    # Hash + persist + invalidate token (single atomic commit so a
    # mid-flight failure doesn't leave the token spent without the
    # password actually changing).
    try:
        user.password_hash = hash_password(req.new_password)
        user.password_reset_token = None
        user.password_reset_expires = None
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.exception("[reset_password] commit failed for user %s: %s", user.id, e)
        raise HTTPException(status_code=500, detail="Could not save the new password. Try again.")

    # Issue a fresh JWT so the user is logged in immediately.
    new_token = create_access_token(user_id=user.id, email=user.email)

    return {
        "ok": True,
        "token": new_token,
        "user_id": user.id,
        "profile": _user_profile(user),
    }


# ---------------------------------------------------------------------------
# PATCH /users/profile
# ---------------------------------------------------------------------------

class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    is_public: Optional[bool] = None
    hive_consent: Optional[bool] = None

@app.patch('/users/profile', summary='Update user profile')
async def update_profile(
    req: ProfileUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')

    if req.name:
        user.name = req.name
    if req.username:
        # Normalise to URL-safe lowercase, then check uniqueness
        cleaned = re.sub(r'[^a-z0-9_]', '', req.username.lower())[:30]
        if not cleaned:
            raise HTTPException(status_code=400, detail='Username must contain letters or numbers')
        existing = await get_user_by_username(db, cleaned)
        if existing and existing.id != user_id:
            raise HTTPException(status_code=400, detail='Username already taken')
        user.username = cleaned
    if req.is_public is not None:
        user.is_public = bool(req.is_public)
    if req.hive_consent is not None:
        # If the user is opting OUT of hive participation, prune their existing
        # signals immediately so their data is not in tonight's batch jobs.
        new_consent = bool(req.hive_consent)
        was_consenting = bool(user.hive_consent)
        user.hive_consent = new_consent
        if was_consenting and not new_consent:
            try:
                from hive import prune_non_consenting_signals
                # Commit the consent flip first so prune sees the new value
                await db.commit()
                pruned = await prune_non_consenting_signals(db)
                logger.info(f"hive_consent revoked by {user_id}, pruned {pruned} signals")
            except Exception as e:
                logger.warning(f"Failed to prune signals after consent revoke for {user_id}: {e}")

    await db.commit()
    await db.refresh(user)
    return _user_profile(user)


# ---------------------------------------------------------------------------
# PATCH /users/birth — update birth details + recompute blueprint
# ---------------------------------------------------------------------------

class BirthUpdateRequest(BaseModel):
    # Strict shapes so a malformed payload cannot reach build_blueprint and
    # silently corrupt the user's stored chart. Pydantic rejects on first
    # mismatch, returning 422 before we touch the DB.
    birth_date: str   = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$', example='1989-09-05', description='YYYY-MM-DD')
    birth_time: str   = Field(..., pattern=r'^\d{2}:\d{2}$',       example='12:30',      description='HH:MM (24h)')
    birth_city: Optional[str]   = Field(None, max_length=255)
    birth_lat:  Optional[float] = Field(None, ge=-90.0,  le=90.0)
    birth_lon:  Optional[float] = Field(None, ge=-180.0, le=180.0)

@app.patch('/users/birth', summary='Update birth details and regenerate blueprint')
async def update_birth(
    req: BirthUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Updates the user's birth_date/time/city and rebuilds the full blueprint.

    A successful update replaces the stored blueprint, so all downstream
    surfaces (today, chart, souls, chat memory) reflect the corrected
    chart on the next read. The current day's cached forecast is also
    invalidated — it would be calculated from stale natal data otherwise.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')

    # Hard parseability checks — Pydantic verified the SHAPE, not that the
    # values are real. "9999-99-99" matches the regex; datetime.strptime
    # is the gate that rejects it.
    try:
        datetime.strptime(req.birth_date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail='birth_date is not a real calendar date.')
    try:
        datetime.strptime(req.birth_time, '%H:%M')
    except ValueError:
        raise HTTPException(status_code=400, detail='birth_time must be a valid 24h time (HH:MM).')

    # Geocode the city if no lat/lon supplied
    birth_lat = req.birth_lat
    birth_lon = req.birth_lon
    if (birth_lat is None or birth_lon is None) and req.birth_city:
        try:
            from astrology import geocode_city
            geo = geocode_city(req.birth_city)
            if geo:
                birth_lat = birth_lat if birth_lat is not None else geo.get('lat')
                birth_lon = birth_lon if birth_lon is not None else geo.get('lon')
        except Exception:
            pass

    if birth_lat is None or birth_lon is None:
        raise HTTPException(
            status_code=400,
            detail='Could not resolve birth location. Provide a recognized city or lat/lon directly.'
        )

    tz_offset = get_tz_offset(birth_lat, birth_lon, req.birth_date, req.birth_time)

    try:
        blueprint = engines.build_blueprint(
            birth_date=req.birth_date,
            birth_time=req.birth_time,
            birth_city=req.birth_city,
            birth_lat=birth_lat,
            birth_lon=birth_lon,
            tz_offset=tz_offset,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Blueprint calculation failed: {str(e)}')

    # Persist new birth data + new blueprint in a SINGLE transaction so a
    # mid-flow failure can't leave the user with new birth fields but a
    # stale blueprint (which would show the wrong chart everywhere). We
    # also wipe today's forecast in the same transaction; it was computed
    # against the old natal chart.
    import json as _json
    import uuid as _uuid
    from sqlalchemy import select as _select, delete as _delete
    from db.database import Blueprint as _Blueprint, DailyForecast as _DailyForecast

    try:
        user.birth_date = req.birth_date
        user.birth_time = req.birth_time
        user.birth_city = req.birth_city
        user.birth_lat  = birth_lat
        user.birth_lon  = birth_lon

        # Upsert blueprint within the SAME session — do not commit yet.
        bp_existing = await db.execute(_select(_Blueprint).where(_Blueprint.user_id == user_id))
        bp = bp_existing.scalar_one_or_none()
        bp_json = _json.dumps(blueprint)
        if bp:
            bp.blueprint_json = bp_json
            bp.updated_at = datetime.utcnow()
        else:
            db.add(_Blueprint(id=str(_uuid.uuid4()), user_id=user_id, blueprint_json=bp_json))

        # Drop today's cached forecast — same session, same commit.
        today_str = date.today().isoformat()
        await db.execute(
            _delete(_DailyForecast).where(
                _DailyForecast.user_id == user_id,
                _DailyForecast.forecast_date == today_str,
            )
        )

        await db.commit()
        await db.refresh(user)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f'Failed to save birth data: {str(e)}')

    return {'profile': _user_profile(user), 'blueprint': blueprint}


# ---------------------------------------------------------------------------
# PATCH /users/photo
# ---------------------------------------------------------------------------

class PhotoUpdateRequest(BaseModel):
    photo: str  # base64 data URI, e.g. "data:image/jpeg;base64,..."

@app.patch('/users/photo', summary='Upload or update profile photo')
async def update_photo(
    req: PhotoUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Store the user's profile photo as a base64 data URI so it syncs across devices."""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')

    # Basic validation: must be a data URI image
    if not req.photo.startswith('data:image/'):
        raise HTTPException(status_code=400, detail='Photo must be a base64 image data URI')

    # Limit size to ~2MB (base64 ~2.7MB raw max, which covers a reasonable profile photo)
    if len(req.photo) > 3_000_000:
        raise HTTPException(status_code=413, detail='Photo too large. Please use an image under 2MB.')

    user.profile_photo = req.photo
    await db.commit()
    return {'ok': True}

# ---------------------------------------------------------------------------
# GET /users/search
# ---------------------------------------------------------------------------

@app.get('/users/search', summary='Search users by username or email')
async def search_users_endpoint(
    q: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Search for users by @username prefix or exact email.
    Returns public-safe fields only: id, username, name, sun_sign, hd_type, hd_profile.
    """
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail='Search query must be at least 2 characters')

    users = await search_users(db, q.strip(), exclude_user_id=user_id)
    results = []
    for u in users:
        bp = await get_blueprint(db, u.id)
        sun_sign = None
        hd_type = None
        hd_profile = None
        if bp:
            summary = bp.get('summary', {})
            hd = bp.get('human_design', {})
            planets = bp.get('astrology', {}).get('natal', {}).get('planets', {})
            sun_sign = summary.get('sun_sign') or planets.get('Sun', {}).get('sign')
            hd_type = summary.get('hd_type') or hd.get('type')
            hd_profile = summary.get('hd_profile') or hd.get('profile')
        results.append({
            'id':         u.id,
            'username':   u.username,
            'name':       u.name,
            'sun_sign':   sun_sign,
            'hd_type':    hd_type,
            'hd_profile': hd_profile,
        })

    return {'results': results, 'count': len(results)}


# ---------------------------------------------------------------------------
# GET /users/{user_id}/public-profile — view a connection's profile if public
# ---------------------------------------------------------------------------

@app.get('/users/{target_user_id}/public-profile', summary='View a soul connection\'s profile')
async def get_public_profile(
    target_user_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return full profile + blueprint for a target user — but only if:
       1. The viewer has an accepted soul connection with the target, AND
       2. The target has set is_public=true.
    Otherwise return a minimal shape with is_public=false so the UI can
    show a 'private' indicator without leaking any chart data.
    """
    target = await get_user_by_id(db, target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail='User not found')

    # Self-view: always allowed, returns the full payload regardless of
    # the user's own is_public flag (it's THEIR data). Saves the caller
    # from special-casing /profile/{my_own_id} in the UI.
    if target_user_id == user_id:
        blueprint = await get_blueprint(db, user_id)
        return {
            'id':            target.id,
            'username':      target.username,
            'name':          target.name,
            'profile_photo': getattr(target, 'profile_photo', None),
            'is_public':     bool(getattr(target, 'is_public', False)),
            'birth_date':    target.birth_date,
            'birth_time':    target.birth_time,
            'birth_city':    target.birth_city,
            'blueprint':     blueprint,
            'is_self':       True,
        }

    # Connection check: scan accepted souls for either direction
    connections = await get_accepted_souls(db, user_id)
    is_connected = any(
        (c.requester_id == user_id and c.recipient_id == target_user_id) or
        (c.recipient_id == user_id and c.requester_id == target_user_id)
        for c in connections
    )
    if not is_connected:
        raise HTTPException(status_code=403, detail='Not a soul connection')

    # Always-safe minimal payload
    minimal = {
        'id':         target.id,
        'username':   target.username,
        'name':       target.name,
        'profile_photo': getattr(target, 'profile_photo', None),
        'is_public':  bool(getattr(target, 'is_public', False)),
    }

    if not minimal['is_public']:
        return minimal

    # Public — surface the full chart + blueprint, but NOT the raw birth
    # coordinates (date, time, city). The chart already encodes everything
    # the connection needs to read who this person is; the raw birth data
    # is the most identifying piece and stays with the person. The /users/me
    # path above keeps these fields for the self view so users can still
    # see and edit their own birth details on /profile/settings.
    blueprint = await get_blueprint(db, target_user_id)
    return {
        **minimal,
        'blueprint': blueprint,
    }


# ---------------------------------------------------------------------------
# GET /forecast/today
# ---------------------------------------------------------------------------

@app.get('/first-mirror', summary="Generate the post-onboarding First Mirror three lines")
async def first_mirror(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Three precise lines that prove Solray understood the user, shown
    immediately after onboarding before /today, before /chat. Single
    LLM call against the user's blueprint. Caller should fall back
    gracefully (skip the screen, route straight to /today) if this
    errors, never invent content.

    Codex UX hook 1, the "First Mirror" pattern: pattern they lead
    with, place they hide their power, question their design returns to.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    bp_row = await get_blueprint(db, user_id)
    if not bp_row:
        raise HTTPException(status_code=404, detail="Blueprint not found, complete onboarding first")
    try:
        import json as _json
        blueprint = _json.loads(bp_row.blueprint_json) if bp_row.blueprint_json else {}
    except Exception:
        blueprint = {}
    if 'meta' not in blueprint:
        blueprint['meta'] = {}
    if not blueprint['meta'].get('name'):
        blueprint['meta']['name'] = user.name

    try:
        from ai.first_mirror import generate_first_mirror
        result = generate_first_mirror(blueprint)
        return result
    except Exception as e:
        logger.exception(f"[first_mirror] failed for user {user_id}: {e}")
        try:
            import sentry_sdk as _sentry
            _sentry.capture_exception(e)
        except Exception:
            pass
        raise HTTPException(status_code=503, detail="First Mirror generation is temporarily unavailable.")


@app.get('/forecast/today', summary="Get today's personalised AI-generated forecast")
async def forecast_today(
    refresh: bool = False,
    user_id: str = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns today's personalised AI-generated forecast for the authenticated user.

    The forecast is generated once per day and cached. It includes:
      - AI-generated day_title and reading (Higher Self voice)
      - tags (astrology, human_design, gene_keys)
      - energy (mental, emotional, physical, intuitive — 1-10)
      - morning_greeting (personalised opening for the chat screen)
      - dominant_transit — the most significant planetary aspect today
      - hd_gate_today — today's active HD gate with Gene Key shadow/gift

    Use ?refresh=true to force regeneration (bypasses cache).
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')

    today_str = date.today().isoformat()

    # Check cache first (unless refresh=true)
    if not refresh:
        cached = await get_cached_forecast(db, user_id, today_str)
        if cached:
            # Return if cached forecast already has AI fields (not an error result)
            if ('day_title' in cached or 'title' in cached) and 'reading' in cached and '_ai_error' not in cached:
                cached['_cached'] = True
                return cached

    # Load the stored blueprint (to avoid recalculating natal chart)
    blueprint = await get_blueprint(db, user_id)

    # Calculate fresh forecast data (ephemeris)
    try:
        forecast_data = engines.get_daily_forecast(
            birth_date=user.birth_date,
            birth_time=user.birth_time,
            birth_city=user.birth_city,
            birth_lat=user.birth_lat,
            birth_lon=user.birth_lon,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Forecast calculation failed: {str(e)}')

    # If we have a blueprint, generate the AI forecast
    ai_forecast = {}
    if blueprint:
        try:
            ai_forecast = generate_daily_forecast(blueprint, forecast_data)
        except Exception as e:
            # AI generation failed — fall back to raw forecast data
            logger.exception(f"AI forecast generation failed for user {user_id}")
            try:
                import sentry_sdk as _sentry
                _sentry.capture_exception(e)
            except Exception:
                logger.warning("Sentry not available for error reporting")
            ai_forecast = {'_ai_error': str(e)}

    # Merge: AI fields + raw data for richer access
    final_forecast = {
        **forecast_data,
        **ai_forecast,
        '_cached': False,
        '_generated_at': datetime.utcnow().isoformat(),
    }

    # Inject deterministic energy scores (overrides any AI-estimated values)
    natal_planets = (blueprint or {}).get('astrology', {}).get('natal', {}).get('planets', {})
    aspects = forecast_data.get('aspects', [])
    energy_scores = calculate_energy_scores(aspects, natal_planets)
    final_forecast['energy'] = energy_scores
    final_forecast['energy_levels'] = {k: v * 10 for k, v in energy_scores.items()}

    # Also add the legacy summary for backward compat
    if 'summary' not in final_forecast:
        final_forecast['summary'] = _build_forecast_summary(forecast_data)

    # Inject lunar phase event if within 3-day window
    natal_chart = (blueprint or {}).get('astrology', {}).get('natal', {})
    try:
        lunar_event = get_upcoming_lunar_event(natal_chart, days_window=3)
        if lunar_event:
            final_forecast['lunar_event'] = lunar_event
    except Exception as _lunar_err:
        # Lunar detection failure should never block the forecast
        pass

    # Cache the full result
    await cache_forecast(db, user_id, today_str, final_forecast)

    return final_forecast


def _build_forecast_summary(forecast: dict) -> dict:
    """
    Generate a structured human-readable summary from raw forecast data.
    Used by the UI to render a daily reading without parsing raw JSON.
    """
    aspects = forecast.get('aspects', [])
    hd_gates = forecast.get('hd_daily_gates', {})
    gene_keys_today = forecast.get('gene_keys_today', [])
    resonance = forecast.get('gene_key_resonance', [])

    # Top 3 most significant transits
    significant_transits = []
    for a in aspects[:3]:
        significant_transits.append(
            f"{a['transit_planet']} {a['aspect']} natal {a['natal_planet']} "
            f"(orb {a['orb']}° in {a['natal_planet']}'s house {a['natal_house']})"
        )

    # Today's Gene Keys headline
    # todays_gene_keys is a dict with sun_gene_key / earth_gene_key sub-dicts
    gk_headlines = []
    if isinstance(gene_keys_today, dict):
        for role in ('sun_gene_key', 'earth_gene_key'):
            gk = gene_keys_today.get(role)
            if gk:
                gk_headlines.append(
                    f"Gate {gk.get('gate')} ({role.replace('_', ' ').title()}): "
                    f"{gk.get('gift', 'Unknown')} "
                    f"(shadow: {gk.get('shadow', '?')}, siddhi: {gk.get('siddhi', '?')})"
                )
    elif isinstance(gene_keys_today, list):
        for gk in gene_keys_today[:3]:
            gk_headlines.append(
                f"Gate {gk.get('gate')}: {gk.get('gift', 'Unknown')} "
                f"(shadow: {gk.get('shadow', '?')}, siddhi: {gk.get('siddhi', '?')})"
            )

    # Resonant gates (natal + today overlap = areas of special focus)
    resonance_notes = []
    for r in (resonance or [])[:2]:
        # Resonance items may have a 'message' field or 'gift' field
        msg = r.get('message') or f"Gate {r.get('gate')} resonates today — natal {r.get('type', 'activation')}"
        resonance_notes.append(msg)

    return {
        'hd_today': {
            'sun_gate':   hd_gates.get('sun_gate'),
            'earth_gate': hd_gates.get('earth_gate'),
            'sun_sign':   hd_gates.get('sun_sign'),
            'earth_sign': hd_gates.get('earth_sign'),
        },
        'top_transits':    significant_transits,
        'gene_key_themes': gk_headlines,
        'resonance':       resonance_notes,
        'aspect_count':    len(aspects),
    }


# ---------------------------------------------------------------------------
# POST /souls/calculate-blueprint
# ---------------------------------------------------------------------------

@app.post('/souls/calculate-blueprint', summary='Calculate blueprint for a soul (no account needed)')
async def calculate_soul_blueprint(req: SoulBlueprintRequest):
    """Calculate a blueprint from birth data without creating a user account."""
    # Geocode
    try:
        from astrology import geocode_city
        lat, lon = geocode_city(req.birth_city)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Auto-detect timezone
    tz_offset = get_tz_offset(lat, lon, req.birth_date, req.birth_time)

    # Build blueprint
    try:
        blueprint = engines.build_blueprint(
            birth_date=req.birth_date,
            birth_time=req.birth_time,
            birth_city=req.birth_city,
            birth_lat=lat,
            birth_lon=lon,
            tz_offset=tz_offset,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Blueprint calculation failed: {str(e)}')

    return {
        'blueprint': blueprint,
        'profile': {
            'birth_date': req.birth_date,
            'birth_time': req.birth_time,
            'birth_city': req.birth_city,
            'sun_sign': blueprint.get('astrology', {}).get('natal', {}).get('planets', {}).get('Sun', {}).get('sign'),
            'hd_type': blueprint.get('human_design', {}).get('type'),
            'hd_profile': blueprint.get('human_design', {}).get('profile'),
        }
    }


# ---------------------------------------------------------------------------
# POST /souls/invite
# ---------------------------------------------------------------------------

@app.post('/souls/invite', summary='Invite someone as a soul connection', status_code=201)
async def invite_soul(
    req: SoulInviteRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a soul connection invite to another user by @username or email.
    The invited user must already have a Solray account.
    Creates a pending soul_connection record.
    """
    requester = await get_user_by_id(db, user_id)

    # Resolve identifier to a user
    identifier = req.identifier.strip()
    if '@' in identifier and '.' in identifier.split('@')[-1]:
        # Looks like an email
        recipient = await get_user_by_email(db, identifier)
    else:
        # Treat as username (strip leading @)
        uname = identifier.lstrip('@')
        recipient = await get_user_by_username(db, uname)

    if not recipient:
        raise HTTPException(
            status_code=404,
            detail='No Solray account found. Ask them to sign up first!',
        )

    # Can't invite yourself
    if recipient.id == user_id:
        raise HTTPException(status_code=400, detail='Cannot invite yourself')

    # Check for existing invite
    from sqlalchemy import select
    from db.database import SoulConnection
    existing = await db.execute(
        select(SoulConnection).where(
            (SoulConnection.requester_id == user_id) &
            (SoulConnection.recipient_id == recipient.id)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail='Invite already sent to this person')

    invite = await create_soul_invite(db, requester_id=user_id, recipient_id=recipient.id)

    return {
        'invite_id':       invite.id,
        'recipient_name':  recipient.name,
        'recipient_username': recipient.username,
        'status':          invite.status,
        'created_at':      invite.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /souls/accept/{invite_id}
# ---------------------------------------------------------------------------

@app.post('/souls/accept/{invite_id}', summary='Accept a soul connection invite')
async def accept_invite(
    invite_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept a pending soul connection invite.
    Only the recipient of the invite can accept it.
    """
    invite = await get_soul_connection(db, invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail='Invite not found')

    if invite.recipient_id != user_id:
        raise HTTPException(
            status_code=403,
            detail='You can only accept invites sent to you'
        )

    if invite.status != 'pending':
        raise HTTPException(
            status_code=409,
            detail=f'Invite is already {invite.status}'
        )

    accepted = await accept_soul_connection(db, invite_id)

    return {
        'invite_id': accepted.id,
        'status':    accepted.status,
        'message':   'Soul connection accepted!',
    }


# ---------------------------------------------------------------------------
# POST /souls/decline/{invite_id}
# ---------------------------------------------------------------------------

@app.post('/souls/decline/{invite_id}', summary='Decline a soul connection invite')
async def decline_invite(
    invite_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    invite = await get_soul_connection(db, invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail='Invite not found')

    if invite.recipient_id != user_id:
        raise HTTPException(status_code=403, detail='You can only decline invites sent to you')

    if invite.status != 'pending':
        raise HTTPException(status_code=409, detail=f'Invite is already {invite.status}')

    declined = await decline_soul_connection(db, invite_id)
    return {'invite_id': declined.id, 'status': declined.status}


# ---------------------------------------------------------------------------
# GET /souls/pending
# ---------------------------------------------------------------------------

@app.get('/souls/pending', summary='List pending incoming soul connection requests')
async def list_pending_invites(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Returns pending connection requests sent TO the authenticated user."""
    invites = await get_pending_invites_for_user(db, user_id)
    result = []
    for inv in invites:
        requester = await get_user_by_id(db, inv.requester_id)
        if not requester:
            continue
        bp = await get_blueprint(db, inv.requester_id)
        sun_sign = None
        hd_type = None
        hd_profile = None
        if bp:
            summary = bp.get('summary', {})
            hd = bp.get('human_design', {})
            planets = bp.get('astrology', {}).get('natal', {}).get('planets', {})
            sun_sign = summary.get('sun_sign') or planets.get('Sun', {}).get('sign')
            hd_type = summary.get('hd_type') or hd.get('type')
            hd_profile = summary.get('hd_profile') or hd.get('profile')
        result.append({
            'invite_id':   inv.id,
            'requester': {
                'id':         requester.id,
                'username':   requester.username,
                'name':       requester.name,
                'sun_sign':   sun_sign,
                'hd_type':    hd_type,
                'hd_profile': hd_profile,
            },
            'created_at': inv.created_at.isoformat(),
        })
    return {'pending': result, 'count': len(result)}


# ---------------------------------------------------------------------------
# GET /souls
# ---------------------------------------------------------------------------

@app.get('/souls', summary='List all accepted soul connections')
async def list_souls(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a list of all accepted soul connections for the authenticated user.
    Each entry includes the soul's profile + their summary blueprint data.
    """
    connections = await get_accepted_souls(db, user_id)
    result = []
    seen_other_ids = set()  # Deduplicate: prevent same person appearing twice

    for conn in connections:
        # Determine which side is the "other" person
        other_id = conn.recipient_id if conn.requester_id == user_id else conn.requester_id

        # Skip if we've already included this person (mutual invites create 2 records)
        if other_id in seen_other_ids:
            continue
        seen_other_ids.add(other_id)

        other_user = await get_user_by_id(db, other_id)
        if not other_user:
            continue

        # Privacy: only expose chart preview fields if the connection has
        # marked their profile public. Otherwise the souls list shows just
        # name + photo. Compatibility readings still work because the
        # blueprint endpoint and chat endpoint inject the full chart for
        # accepted connections (the connection handshake is the consent
        # for the AI to read both charts together; is_public is the consent
        # for chart data to be displayed in the UI).
        is_public_flag = bool(getattr(other_user, 'is_public', False))
        sun_sign = None
        moon_sign = None
        hd_type = None
        hd_profile = None
        if is_public_flag:
            bp = await get_blueprint(db, other_id)
            if bp:
                summary = bp.get('summary', {})
                hd = bp.get('human_design', {})
                planets = bp.get('astrology', {}).get('natal', {}).get('planets', {})
                sun_sign = summary.get('sun_sign') or planets.get('Sun', {}).get('sign')
                moon_sign = summary.get('moon_sign') or planets.get('Moon', {}).get('sign')
                hd_type = summary.get('hd_type') or hd.get('type')
                hd_profile = summary.get('hd_profile') or hd.get('profile')

        result.append({
            'connection_id': conn.id,
            'soul': {
                'id':            other_user.id,
                'username':      other_user.username,
                'name':          other_user.name,
                'is_public':     is_public_flag,
                'sun_sign':      sun_sign,
                'moon_sign':     moon_sign,
                'hd_type':       hd_type,
                'hd_profile':    hd_profile,
                'profile_photo': getattr(other_user, 'profile_photo', None),
            },
            'connected_since': conn.created_at.isoformat(),
        })

    return {'souls': result, 'count': len(result)}


# ---------------------------------------------------------------------------
# GET /souls/{soul_id}/synergy
# ---------------------------------------------------------------------------

@app.get('/souls/{soul_id}/synergy', summary='Synergy reading between self and a soul connection')
async def soul_synergy(
    soul_id: str,
    user_id: str = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a combined chart synergy reading between the authenticated user
    and a soul connection. Includes both full blueprints and a synergy analysis
    highlighting shared gates, complementary types, and Gene Key resonance.
    """
    # Verify these two users have an accepted connection
    connections = await get_accepted_souls(db, user_id)
    conn = next(
        (c for c in connections
         if c.requester_id == soul_id or c.recipient_id == soul_id),
        None
    )
    if not conn:
        raise HTTPException(
            status_code=403,
            detail='No accepted soul connection found with this user'
        )

    # Load both blueprints
    my_bp   = await get_blueprint(db, user_id)
    soul_bp = await get_blueprint(db, soul_id)
    soul_user = await get_user_by_id(db, soul_id)

    if not my_bp or not soul_bp:
        raise HTTPException(
            status_code=404,
            detail='Blueprint not found for one or both users. Please regenerate.'
        )

    me = await get_user_by_id(db, user_id)

    # Compute synergy analysis
    synergy = _compute_synergy(my_bp, soul_bp)

    return {
        'self': {
            'id':        user_id,
            'name':      me.name if me else 'You',
            'blueprint': my_bp,
        },
        'soul': {
            'id':        soul_id,
            'name':      soul_user.name if soul_user else 'Soul',
            'blueprint': soul_bp,
        },
        'synergy': synergy,
    }


# ---------------------------------------------------------------------------
# GET /souls/{soul_id}/compatibility
# ---------------------------------------------------------------------------

@app.get('/souls/{soul_id}/compatibility', summary='Compatibility reading between self and a soul connection')
async def soul_compatibility(
    soul_id: str,
    user_id: str = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """Returns a four-lens Oracle reading (amplify, misread, safety,
    lesson) plus the structural synergy signals (shared gates, channels,
    HD type pairing). No scoring, no percentage. Used by the embedded
    Compatibility section on /profile/[id].

    Premium gated: paying subscribers only. Both users must be in an
    accepted soul connection.
    """
    # Verify accepted connection
    connections = await get_accepted_souls(db, user_id)
    conn = next(
        (c for c in connections
         if c.requester_id == soul_id or c.recipient_id == soul_id),
        None,
    )
    if not conn:
        raise HTTPException(
            status_code=403,
            detail='No accepted soul connection found with this user',
        )

    my_bp = await get_blueprint(db, user_id)
    soul_bp = await get_blueprint(db, soul_id)
    me = await get_user_by_id(db, user_id)
    them = await get_user_by_id(db, soul_id)

    if not my_bp or not soul_bp:
        raise HTTPException(
            status_code=404,
            detail='Blueprint not found for one or both users.',
        )

    user_name = (me.name if me else 'You')
    soul_name = (them.name if them else 'Soul')

    # Structural signals (no AI). Drop legacy resonance_score; the new
    # multi-axis Resonance Index lives in `index` and is the canonical
    # quantitative read on the pair.
    signals = _compute_synergy(my_bp, soul_bp)
    signals.pop('resonance_score', None)

    # Solray Resonance Index — transparent, deterministic 0-100 with
    # five sub-axes. Replaces the opaque single-number we deliberately
    # avoided earlier.
    from souls.resonance_index import compute_resonance_index
    index = compute_resonance_index(my_bp, soul_bp)

    # Oracle four-lens reading.
    from ai.compatibility import generate_compatibility_reading
    reading = generate_compatibility_reading(my_bp, soul_bp, user_name, soul_name)

    return {
        'self': {'id': user_id, 'name': user_name},
        'soul': {'id': soul_id, 'name': soul_name},
        'index': index,
        'signals': signals,
        'reading': reading,
    }


# ---------------------------------------------------------------------------
# GET /souls/{connection_id}/blueprint
# ---------------------------------------------------------------------------

@app.get('/souls/{connection_id}/blueprint', summary="Get a soul connection's full blueprint (for chat only)")
async def soul_blueprint_for_chat(
    connection_id: str,
    user_id: str = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the full blueprint for a soul connection.
    Only accessible by either party in the accepted connection.
    """
    conn = await get_soul_connection(db, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail='Connection not found')

    if conn.status != 'accepted':
        raise HTTPException(status_code=403, detail='Connection is not accepted')

    if user_id not in (conn.requester_id, conn.recipient_id):
        raise HTTPException(status_code=403, detail='Access denied')

    other_id = conn.recipient_id if conn.requester_id == user_id else conn.requester_id
    other_user = await get_user_by_id(db, other_id)
    bp = await get_blueprint(db, other_id)

    if not bp:
        raise HTTPException(status_code=404, detail='Blueprint not found for this soul')

    return {
        'connection_id': connection_id,
        'soul': {
            'id':       other_user.id if other_user else other_id,
            'username': other_user.username if other_user else None,
            'name':     other_user.name if other_user else 'Unknown',
        },
        'blueprint': bp,
    }


# ---------------------------------------------------------------------------
# POST /chat/group
# ---------------------------------------------------------------------------

@app.post('/chat/group', summary='Group compatibility chat with a soul connection')
async def group_chat_endpoint(
    req: GroupChatRequest,
    user_id: str = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """
    Group chat where both users are in the same thread.
    The AI holds both blueprints and responds as a shared Higher Self guide.
    """
    # Validate the connection
    conn = await get_soul_connection(db, req.soul_connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail='Connection not found')

    if conn.status != 'accepted':
        raise HTTPException(status_code=403, detail='Connection is not accepted')

    if user_id not in (conn.requester_id, conn.recipient_id):
        raise HTTPException(status_code=403, detail='You are not part of this connection')

    # Load both blueprints
    my_bp = await get_blueprint(db, user_id)
    other_id = conn.recipient_id if conn.requester_id == user_id else conn.requester_id
    soul_bp = await get_blueprint(db, other_id)

    if not my_bp or not soul_bp:
        raise HTTPException(status_code=404, detail='Blueprint not found for one or both users')

    me = await get_user_by_id(db, user_id)
    other_user = await get_user_by_id(db, other_id)

    user_name = me.name if me else 'You'
    soul_name = other_user.name if other_user else 'Soul'

    history = [{'role': m.role, 'content': m.content} for m in req.conversation_history]

    try:
        response = group_higher_self_chat(
            user_blueprint=my_bp,
            soul_blueprint=soul_bp,
            user_name=user_name,
            soul_name=soul_name,
            conversation_history=history,
            sender_name=req.sender_username,
            message=req.message,
        )

        # Voice-consistency audit. Same fire-and-forget pattern as /chat,
        # using _spawn_background so the Task is held by a strong ref
        # and cannot be GC'd mid-flight (Codex audit). Suffix the
        # model_used with -group so the dashboard can separate
        # one-on-one drift from group-conversation drift, since the
        # rules apply differently when two charts are present.
        try:
            from ai.audit import audit_oracle_reply
            from ai.chat import LAST_MODEL_USED, get_oracle_prompt_version
            _spawn_background(audit_oracle_reply(
                user_id=user_id,
                user_message=req.message,
                oracle_reply=response or "",
                model_used=f"{LAST_MODEL_USED.get()}-group",
                oracle_prompt_version=get_oracle_prompt_version(),
            ))
        except Exception as _audit_err:
            logger.warning(f"[audit] schedule failed for group chat: {_audit_err}")

        return {'response': response}
    except Exception as e:
        logger.exception(f"Group chat error for connection {req.soul_connection_id}")
        try:
            import sentry_sdk as _sentry
            _sentry.capture_exception(e)
        except Exception:
            logger.warning("Sentry not available for error reporting")
        raise HTTPException(status_code=500, detail=f'Group chat error: {str(e)}')


def _compute_synergy(bp_a: dict, bp_b: dict) -> dict:
    """
    Compute synergy metrics between two blueprints.
    Returns a dict with shared gates, complementary channels, and Gene Key overlap.
    """
    gates_a = set(bp_a.get('human_design', {}).get('active_gates', []))
    gates_b = set(bp_b.get('human_design', {}).get('active_gates', []))
    shared_gates = sorted(gates_a & gates_b)
    unique_a = sorted(gates_a - gates_b)
    unique_b = sorted(gates_b - gates_a)

    # Complementary: gate pairs that form channels (HD channels connect two gates)
    # Check if any of A's unique gates + B's unique gates form complete channels
    channels_a = set(tuple(sorted(ch)) for ch in bp_a.get('human_design', {}).get('defined_channels', []))
    channels_b = set(tuple(sorted(ch)) for ch in bp_b.get('human_design', {}).get('defined_channels', []))
    shared_channels = [list(c) for c in channels_a & channels_b]

    # Gene Key resonance (shared gift themes)
    gk_a = {gk['gate']: gk for gk in bp_a.get('gene_keys', {}).get('profile', [])}
    gk_b = {gk['gate']: gk for gk in bp_b.get('gene_keys', {}).get('profile', [])}
    shared_gene_keys = []
    for gate in shared_gates:
        if gate in gk_a and gate in gk_b:
            shared_gene_keys.append({
                'gate':   gate,
                'gift':   gk_a[gate].get('gift'),
                'shadow': gk_a[gate].get('shadow'),
                'siddhi': gk_a[gate].get('siddhi'),
            })

    # Type compatibility note
    type_a = bp_a.get('human_design', {}).get('type', 'Unknown')
    type_b = bp_b.get('human_design', {}).get('type', 'Unknown')
    compatibility_note = _hd_type_compatibility(type_a, type_b)

    return {
        'shared_gates':       shared_gates,
        'shared_gates_count': len(shared_gates),
        'unique_to_self':     unique_a,
        'unique_to_soul':     unique_b,
        'shared_channels':    shared_channels,
        'shared_gene_keys':   shared_gene_keys,
        'hd_types': {
            'self': type_a,
            'soul': type_b,
            'compatibility_note': compatibility_note,
        },
        'resonance_score': _resonance_score(
            len(shared_gates), len(gates_a | gates_b),
            len(shared_channels)
        ),
    }


def _hd_type_compatibility(type_a: str, type_b: str) -> str:
    """Return a short compatibility note for two HD types."""
    combos = {
        frozenset(['Generator', 'Generator']):         'Two Generators — sustained energy and shared rhythm. Build together.',
        frozenset(['Generator', 'Manifesting Generator']): 'Generator meets MG — fast-moving energy with staying power.',
        frozenset(['Manifesting Generator', 'Manifesting Generator']): 'Double MG — explosive, multidimensional co-creation.',
        frozenset(['Generator', 'Projector']):         'Generator + Projector — classic pairing. Projector guides, Generator provides fuel.',
        frozenset(['Manifesting Generator', 'Projector']): 'MG + Projector — high-octane direction. Great for projects that need vision + drive.',
        frozenset(['Manifestor', 'Generator']):        'Manifestor + Generator — initiator and sustainer. Powerful if Manifestor communicates.',
        frozenset(['Manifestor', 'Projector']):        'Two leadership types — respect autonomy, watch for direction clashes.',
        frozenset(['Projector', 'Projector']):         'Two Projectors — deeply insightful together. Need a Generator/MG environment to thrive.',
        frozenset(['Reflector', 'Generator']):         'Reflector + Generator — Reflector mirrors the Generator\'s quality. Beautiful amplification.',
    }
    key = frozenset([type_a, type_b])
    return combos.get(key, f'{type_a} + {type_b} — unique combination. Explore your individual strategies together.')


def _resonance_score(shared: int, total: int, shared_channels: int) -> float:
    """
    Simple resonance score 0–100.
    Higher = more energetic overlap between the two charts.
    """
    if total == 0:
        return 0.0
    gate_score = (shared / total) * 70  # 70% weight
    channel_bonus = min(shared_channels * 5, 30)  # up to 30% from channels
    return round(gate_score + channel_bonus, 1)


# ---------------------------------------------------------------------------
# Payments & Subscription
# ---------------------------------------------------------------------------

class SubscribeRequest(BaseModel):
    """Start a trial. Card details come later via SecurePay hosted flow."""
    pass  # No fields needed: trial starts without card


class AttachCardRequest(BaseModel):
    """Attach a Teya card token after SecurePay callback."""
    teya_token: str
    card_last_four: str
    card_brand: str


@app.post('/subscribe', summary='Start a free trial')
async def subscribe(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Start a 5-day free trial. No card required upfront."""
    existing = await get_subscription(db, user_id)
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Subscription already exists (status: {existing.status})"
        )

    sub = await start_trial(db, user_id)
    return {
        "status": sub.status,
        "trial_end": sub.trial_end.isoformat() if sub.trial_end else None,
        "message": "Your 5-day free trial has started.",
    }


@app.post('/subscribe/card', summary='Attach a payment card')
async def attach_payment_card(
    req: AttachCardRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Store a Teya multi-use token on the subscription and activate it.

    Called after the user completes the SecurePay hosted card form.
    SecurePay already charged the first month, so we activate immediately
    without a second charge.
    """
    sub = await get_subscription(db, user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found. Start a trial first.")

    # Store the token and card details
    sub = await attach_card(
        db, user_id,
        teya_token=req.teya_token,
        card_last_four=req.card_last_four,
        card_brand=req.card_brand,
    )

    # Activate the subscription — first month was already charged by SecurePay
    from datetime import datetime, timedelta
    from payments.subscription_manager import BILLING_CYCLE_DAYS
    now = datetime.utcnow()
    sub.status = "active"
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=BILLING_CYCLE_DAYS)
    sub.updated_at = now
    await db.commit()
    await db.refresh(sub)

    return {
        "status": sub.status,
        "card_brand": sub.card_brand,
        "card_last_four": sub.card_last_four,
        "message": "Subscription activated.",
    }


@app.post('/subscribe/activate', summary='Convert trial to paid (charge card now)')
async def activate_subscription(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Skip remaining trial and start paid subscription immediately."""
    try:
        sub = await convert_trial_to_active(db, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": sub.status,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "message": "Subscription activated." if sub.status == "active" else "Payment failed, will retry.",
    }


@app.post('/subscribe/cancel', summary='Cancel subscription')
async def cancel_sub(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Cancel the subscription. Access continues until period end."""
    try:
        sub = await cancel_subscription(db, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": sub.status,
        "access_until": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "message": "Subscription cancelled.",
    }


@app.get('/subscribe/status', summary='Get subscription status')
async def subscription_status(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    response: Response = None,
):
    """Return current subscription state and access level.
    
    Cache-Control: no-cache, no-store, must-revalidate ensures the browser
    never serves stale subscription data. Payment activation must be visible
    immediately, not 5 minutes later.
    """
    # Force no caching
    if response:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    
    sub = await get_subscription(db, user_id)
    if not sub:
        return {
            "subscribed": False,
            "status": None,
            "has_access": False,
        }

    return {
        "subscribed": True,
        "status": sub.status,
        "has_access": has_premium_access(sub),
        "trial_end": sub.trial_end.isoformat() if sub.trial_end else None,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "card_brand": sub.card_brand,
        "card_last_four": sub.card_last_four,
        "price": f"${sub.price_amount / 100:.2f}",
        "cancelled_at": sub.cancelled_at.isoformat() if sub.cancelled_at else None,
    }


@app.post('/subscribe/securepay', summary='Create a SecurePay session for card entry')
async def create_securepay(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Generate a Borgun SecurePay hosted page URL.
    The frontend redirects the user there to enter card details.
    On success, Borgun redirects back to /subscribe/teya-return with a token.

    We persist a PaymentEvent with event_type='session_created' and the
    SecurePay orderid in teya_transaction_id so the return-callback (which
    has no user cookie) can map orderid -> user_id and activate the sub.
    """
    sub = await get_subscription(db, user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found. Start a trial first.")

    try:
        backend_base = os.environ.get("BACKEND_BASE_URL", "https://solray-backend-production.up.railway.app")
        return_url = f"{backend_base}/subscribe/teya-return"
        result = await teya.create_securepay_session(
            return_url=return_url,
            cancel_url=return_url,
            amount=sub.price_amount,  # Charge first month via SecurePay hosted form
            success_server_url=return_url,  # Teya requires returnurlsuccessserver to match returnurlsuccess
        )

        session_url = result.get("SessionUrl") or result.get("url")
        session_token = result.get("SessionToken")  # This is the SecurePay orderid

        # Persist orderid -> user_id mapping so the return callback (which
        # comes back on the browser with no auth context) can find who to
        # activate. Use an immutable PaymentEvent row, so we also get an
        # audit trail of every checkout attempt.
        if session_token:
            from payments.subscription_manager import _log_event
            await _log_event(
                db,
                user_id=user_id,
                subscription_id=sub.id,
                event_type="session_created",
                amount=sub.price_amount,
                currency=sub.price_currency,
                teya_transaction_id=str(session_token),
                teya_status="pending",
                teya_response=None,
            )
            await db.commit()

        return {
            "session_url": session_url,
            "session_token": session_token,
        }
    except TeyaError as e:
        logger.error("[payments] SecurePay session creation failed: %s", e.message)
        # Persist a session_failed PaymentEvent so the canary detects the
        # outage even if no end-user complains. Best-effort; never let
        # this masking write fail the original error response.
        try:
            from payments.subscription_manager import _log_event
            await _log_event(
                db,
                user_id=user_id,
                subscription_id=sub.id if sub else None,
                event_type="session_failed",
                amount=sub.price_amount if sub else 0,
                currency=sub.price_currency if sub else "USD",
                teya_transaction_id=None,
                teya_status="error",
                teya_response=str(e.message)[:2000],
            )
            await db.commit()
        except Exception as _e:
            logger.warning("[payments] could not log session_failed event: %s", _e)
        raise HTTPException(status_code=502, detail="Could not create payment session. Please try again.")


@app.api_route('/subscribe/teya-return', methods=["GET", "POST"], summary='Teya SecurePay return callback')
async def teya_return(request: Request, db: AsyncSession = Depends(get_db)):
    """Return URL for Teya SecurePay. Borgun sends the browser (and, if
    configured, a server-to-server POST) back here after card entry.

    Responsibilities:
      1. Log every parameter Teya sent (audit + debugging).
      2. Look up the user who started this checkout via PaymentEvent where
         event_type='session_created' AND teya_transaction_id=Orderid.
      3. If successful, call attach_card() + mark subscription active and
         log a 'charge' PaymentEvent.
      4. Redirect the browser to the frontend:
         - success -> /subscribe?activated=1 (page re-fetches status)
         - failure -> /subscribe/cancelled?<teya error params>
    """
    from payments.models import PaymentEvent, Subscription
    from payments.subscription_manager import _log_event, BILLING_CYCLE_DAYS
    from datetime import datetime, timedelta
    from sqlalchemy import select

    qp = dict(request.query_params)
    form = {}
    try:
        form = dict(await request.form())
    except Exception:
        pass

    logger.warning(
        "[Teya] return callback\n"
        " method: %s\n"
        " path: %s\n"
        " query: %s\n"
        " form: %s\n"
        " headers: %s",
        request.method,
        request.url.path,
        qp,
        form,
        {k: v for k, v in request.headers.items() if k.lower() not in {"cookie", "authorization"}},
    )

    merged = {**qp, **form}

    # Case-insensitive lookup helper: Borgun mixes casing (Orderid vs orderid,
    # Token vs token, etc.) between product versions.
    def pick(*names: str) -> str:
        lowered = {k.lower(): v for k, v in merged.items()}
        for n in names:
            v = lowered.get(n.lower())
            if v:
                return v
        return ""

    order_id = pick("Orderid", "OrderID", "orderid", "order_id", "reference")
    token = pick("Token", "token")
    masked_pan = pick("pan", "PAN", "maskedpan", "MaskedPAN", "cardnumbermasked", "CardNumberMasked")
    card_type = pick("card_type", "cardtype", "CardType", "cardbrand", "CardBrand")
    status_raw = pick("Status", "status", "ResponseCode", "responsecode", "ActionCode", "actioncode")
    status_val = status_raw.lower()
    transaction_id = pick("transactionid", "TransactionID", "transaction_id", "paymentid", "PaymentID")

    frontend = "https://app.solray.ai"

    # Derive success: explicit status, OR presence of a token (Borgun sometimes
    # omits a status field entirely on success and only sends token + pan).
    is_success = bool(token) or status_val in {"success", "ok", "approved", "completed", "000"}

    # Failure path: redirect to cancelled with full query string so the user
    # (and we) can see why. Also persist a 'charge_failed' PaymentEvent so
    # the canary can detect systemic failures without scraping logs.
    if not is_success:
        try:
            # Attempt to find the user via order_id so the failed event is
            # attributable. If we can't, log a sentinel row anyway — the
            # canary just counts these.
            failed_user_id = None
            failed_sub_id = None
            if order_id:
                _r = await db.execute(
                    select(PaymentEvent)
                    .where(PaymentEvent.event_type == "session_created")
                    .where(PaymentEvent.teya_transaction_id == str(order_id))
                    .order_by(PaymentEvent.created_at.desc())
                    .limit(1)
                )
                _ev = _r.scalar_one_or_none()
                if _ev:
                    failed_user_id = _ev.user_id
                    failed_sub_id  = _ev.subscription_id
            if failed_user_id and failed_sub_id:
                await _log_event(
                    db,
                    user_id=failed_user_id,
                    subscription_id=failed_sub_id,
                    event_type="charge_failed",
                    amount=0,
                    currency="USD",
                    teya_transaction_id=str(order_id) if order_id else None,
                    teya_status=status_raw or "failed",
                    teya_response=str(merged)[:2000],
                )
                await db.commit()
        except Exception as _e:
            logger.warning("[Teya] could not log charge_failed event: %s", _e)
        target = f"{frontend}/subscribe/cancelled?{request.url.query}"
        logger.warning("[Teya] callback treated as failure, redirecting to %s", target)
        return RedirectResponse(target, status_code=302)

    # Success path: find who this checkout belongs to.
    user_id: Optional[str] = None
    sub_obj: Optional[object] = None
    if order_id:
        result = await db.execute(
            select(PaymentEvent)
            .where(PaymentEvent.event_type == "session_created")
            .where(PaymentEvent.teya_transaction_id == str(order_id))
            .order_by(PaymentEvent.created_at.desc())
            .limit(1)
        )
        event_row = result.scalar_one_or_none()
        if event_row:
            user_id = event_row.user_id
            sub_lookup = await db.execute(
                select(Subscription).where(Subscription.id == event_row.subscription_id)
            )
            sub_obj = sub_lookup.scalar_one_or_none()

    if not user_id or not sub_obj:
        logger.error(
            "[Teya] return callback could not resolve user for orderid=%r. "
            "Redirecting to /subscribe so the user can retry.",
            order_id,
        )
        target = f"{frontend}/subscribe?activation=unknown"
        return RedirectResponse(target, status_code=302)

    # Idempotency: if this orderid already has a 'charge' event, we've
    # already activated this checkout. Just redirect the user to the
    # success page without touching the DB again. Protects against Borgun
    # firing both a server-to-server POST and a browser GET, or the user
    # hitting back + forward in their browser.
    already_charged = await db.execute(
        select(PaymentEvent)
        .where(PaymentEvent.event_type == "charge")
        .where(PaymentEvent.teya_transaction_id == str(order_id))
        .limit(1)
    )
    if already_charged.scalar_one_or_none():
        logger.info(
            "[Teya] return callback is a duplicate for orderid=%s, skipping re-activation",
            order_id,
        )
        target = f"{frontend}/subscribe/welcome"
        return RedirectResponse(target, status_code=302)

    # Derive card_last_four from masked pan (keep only the last 4 digits).
    last_four = ""
    if masked_pan:
        digits = "".join(c for c in masked_pan if c.isdigit())
        if digits:
            last_four = digits[-4:]

    brand = (card_type or "Card").title()

    # Write card details + flip to active. We do this inline (rather than
    # calling attach_card + second commit) so it's a single transaction.
    now = datetime.utcnow()
    sub_obj.teya_token = token or sub_obj.teya_token
    if last_four:
        sub_obj.card_last_four = last_four
    if brand:
        sub_obj.card_brand = brand
    sub_obj.status = "active"
    sub_obj.current_period_start = now
    sub_obj.current_period_end = now + timedelta(days=BILLING_CYCLE_DAYS)
    sub_obj.retry_count = 0
    sub_obj.next_retry_at = None
    sub_obj.updated_at = now

    # Audit the successful first charge. SecurePay charged us before sending
    # the browser back, so this row represents the real money movement.
    await _log_event(
        db,
        user_id=user_id,
        subscription_id=sub_obj.id,
        event_type="charge",
        amount=sub_obj.price_amount,
        currency=sub_obj.price_currency,
        teya_transaction_id=transaction_id or str(order_id),
        teya_status=status_raw or "success",
        teya_response=str(merged),
    )
    await db.commit()

    logger.warning(
        "[Teya] subscription activated via SecurePay return: user=%s sub=%s order=%s token=%s brand=%s ..%s",
        user_id, sub_obj.id, order_id, (token or "")[:6] + "...", brand, last_four,
    )

    target = f"{frontend}/subscribe/welcome"
    return RedirectResponse(target, status_code=302)


@app.post('/admin/billing-cycle', summary='Manually trigger a billing cycle (admin only)')
async def trigger_billing_cycle(
    admin_id: str = Depends(require_admin),
):
    """Run one billing cycle immediately. For debugging and manual intervention."""
    from payments.billing_scheduler import run_billing_cycle
    await run_billing_cycle()
    return {"message": "Billing cycle complete."}


# ---------------------------------------------------------------------------
# Hive Mind admin endpoints (Phases 1-5 dark-launch)
# ---------------------------------------------------------------------------
# These endpoints fire each phase on demand. None of them are reachable
# from any user-facing path. The hive engine in hive.py does not run unless
# one of these endpoints is hit. RAG integration into the Oracle's chat
# prompt is intentionally NOT wired here; that ships separately once Bob
# confirms the data quality from these inspection endpoints.
# ---------------------------------------------------------------------------

@app.post('/admin/hive/discover', summary='Phase 1: rebuild pattern_cohorts from current chart_components (admin only)')
async def admin_hive_discover(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from hive import discover_cohorts
    return await discover_cohorts(db)


@app.post('/admin/hive/correlations', summary='Phase 1: rebuild pattern_correlations from current chart_components (admin only)')
async def admin_hive_correlations(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from hive import rebuild_correlations
    return await rebuild_correlations(db)


@app.post('/admin/hive/resonance', summary='Phase 3: refresh user_resonance for every consenting user (admin only)')
async def admin_hive_resonance(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from hive import compute_resonance_for_all
    return await compute_resonance_for_all(db)


@app.post('/admin/hive/themes/{user_id}', summary='Phase 4: propagate one user\'s memories into theme emergence (admin only)')
async def admin_hive_themes(
    user_id: str,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from hive import emerge_themes_from_memories
    return await emerge_themes_from_memories(db, user_id)


@app.post('/admin/hive/metrics', summary='Phase 5: write today\'s hive_metrics row (admin only)')
async def admin_hive_metrics(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from hive import write_daily_hive_metrics
    row = await write_daily_hive_metrics(db)
    return {
        'metric_date': row.metric_date.isoformat() if row.metric_date else None,
        'total_users': row.total_users,
        'total_signals': row.total_signals,
        'active_cohorts': row.active_cohorts,
        'avg_cohort_size': row.avg_cohort_size,
        'cohorts_high_confidence': row.cohorts_high_confidence,
        'avg_themes_per_cohort': row.avg_themes_per_cohort,
        'strong_correlations': row.strong_correlations,
        'avg_user_resonance': row.avg_user_resonance,
    }


@app.post('/admin/hive/backfill', summary='Backfill chart_signals for every consenting user with a blueprint (admin only)')
async def admin_hive_backfill(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """One-time-ish backfill. Existing users created before Hive Phase 0
    shipped have a Blueprint row but no chart_signals row. Walk every
    consenting user, load their blueprint, write a signal. Idempotent —
    _write_chart_signal upserts by (user_id, archetype) so re-running this
    just refreshes the existing rows.

    Returns counts: scanned, written, skipped (no blueprint or no consent),
    failed.
    """
    from sqlalchemy import select
    from db.database import User, Blueprint, _write_chart_signal
    import json as _json

    # Pull every consenting user
    user_rows = (await db.execute(
        select(User.id).where(User.hive_consent == True)  # noqa: E712
    )).scalars().all()

    scanned = 0
    written = 0
    skipped_no_blueprint = 0
    failed = 0

    for uid in user_rows:
        scanned += 1
        try:
            bp_row = (await db.execute(
                select(Blueprint).where(Blueprint.user_id == uid)
            )).scalar_one_or_none()
            if not bp_row or not bp_row.blueprint_json:
                skipped_no_blueprint += 1
                continue
            try:
                blueprint = _json.loads(bp_row.blueprint_json)
            except Exception:
                failed += 1
                continue
            await _write_chart_signal(db, uid, blueprint)
            written += 1
        except Exception as e:
            logger.warning(f"hive backfill failed for user {uid}: {e}")
            failed += 1

    return {
        'scanned': scanned,
        'written': written,
        'skipped_no_blueprint': skipped_no_blueprint,
        'failed': failed,
    }


@app.post('/admin/hive/maintenance', summary='Prune signals from non-consenting users (admin only)')
async def admin_hive_maintenance(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from hive import prune_non_consenting_signals
    pruned = await prune_non_consenting_signals(db)
    return {'pruned_signals': pruned}


@app.get('/admin/hive/graph', summary='Hive visualization graph: nodes (users) and edges (shared components) for the dashboard')
async def admin_hive_graph(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return the collective as a node-edge graph for the dashboard.

    Each consenting user is a node carrying their user_id, display name (first
    name only), and the set of components on their chart (sun_sign, hd_type,
    etc). Edges connect users who share at least one component, weighted by
    how many components they share. The frontend renders a force-directed
    layout: central Solray sun, user nodes radiating out, edges drawn between
    chart neighbours, denser as the population grows.

    Counts at the top mirror /admin/hive/inspect for the metrics strip.
    """
    from sqlalchemy import func as _func, select
    from db.database import (
        ChartSignal, ChartComponent, PatternCohort, PatternTheme,
        PatternCorrelation, UserResonance, User,
    )

    # Counts row
    consenting = (await db.execute(
        select(_func.count(User.id)).where(User.hive_consent == True)  # noqa: E712
    )).scalar() or 0
    total_signals = (await db.execute(select(_func.count(ChartSignal.signal_id)))).scalar() or 0
    total_components = (await db.execute(select(_func.count(ChartComponent.component_id)))).scalar() or 0
    total_cohorts = (await db.execute(select(_func.count(PatternCohort.cohort_id)))).scalar() or 0
    total_themes = (await db.execute(select(_func.count(PatternTheme.theme_id)))).scalar() or 0
    total_corrs = (await db.execute(select(_func.count(PatternCorrelation.correlation_id)))).scalar() or 0
    high_conf_cohorts = (await db.execute(
        select(_func.count(PatternCohort.cohort_id)).where(PatternCohort.confidence_score >= 0.8)
    )).scalar() or 0

    # Pull every consenting user's components
    rows = (await db.execute(
        select(
            ChartSignal.user_id,
            ChartComponent.component_type,
            ChartComponent.component_value,
        )
        .join(ChartSignal, ChartSignal.signal_id == ChartComponent.signal_id)
        .join(User, User.id == ChartSignal.user_id)
        .where(User.hive_consent == True)  # noqa: E712
    )).all()

    # Group components per user
    from collections import defaultdict
    user_comps: dict[str, set[str]] = defaultdict(set)
    for uid, ctype, cval in rows:
        user_comps[uid].add(f"{ctype}={cval}")

    # Codename by join order: Sol.00 is the first soul ever, Sol.01 is the
    # second, etc. Carries the lineage of the collective without leaking any
    # personal information. Stable per user — a soul's number never changes.
    # We pad to 2 digits up to 99, then naturally grow to 3 digits past 100.
    user_ids = list(user_comps.keys())
    name_map: dict[str, str] = {}
    sign_map: dict[str, str] = {}
    type_map: dict[str, str] = {}
    if user_ids:
        # Get every consenting user ordered by created_at so the codename
        # reflects true chronological position in the hive, not just within
        # this query's result set.
        all_consenting_ordered = (await db.execute(
            select(User.id).where(User.hive_consent == True).order_by(User.created_at.asc())  # noqa: E712
        )).scalars().all()
        rank_by_id: dict[str, int] = {uid: i for i, uid in enumerate(all_consenting_ordered)}
        for uid in user_ids:
            rank = rank_by_id.get(uid, 0)
            # Natural width, no zero-padding: Sol.0, Sol.1, ..., Sol.9,
            # Sol.10, Sol.99, Sol.100. Reads cleaner than fixed-width.
            name_map[uid] = f"Sol.{rank}"

    # Use sun_sign + hd_type to colour-code nodes by primary cohort dimension
    for uid, comps in user_comps.items():
        for c in comps:
            if c.startswith("sun_sign="):
                sign_map[uid] = c.split("=", 1)[1]
            if c.startswith("hd_type="):
                type_map[uid] = c.split("=", 1)[1]

    # Build nodes
    nodes = []
    for uid in user_ids:
        nodes.append({
            "id": uid,
            "name": name_map.get(uid, "Soul"),
            "sun_sign": sign_map.get(uid),
            "hd_type": type_map.get(uid),
            "component_count": len(user_comps[uid]),
        })

    # Build edges: pair users who share at least one component, weight =
    # count of shared components. For N users this is O(N^2) but at our
    # scale (under a few hundred for a long time) this is trivial.
    edges = []
    for i in range(len(user_ids)):
        for j in range(i + 1, len(user_ids)):
            a, b = user_ids[i], user_ids[j]
            shared = user_comps[a] & user_comps[b]
            if shared:
                edges.append({"a": a, "b": b, "weight": len(shared)})

    # Top cohorts for the side panel
    top_cohorts_rows = (await db.execute(
        select(PatternCohort)
        .order_by(PatternCohort.member_count.desc())
        .limit(8)
    )).scalars().all()
    top_cohorts = [
        {"name": c.cohort_name, "member_count": c.member_count, "confidence": c.confidence_score}
        for c in top_cohorts_rows
    ]

    return {
        "counts": {
            "consenting_users": consenting,
            "chart_signals": total_signals,
            "chart_components": total_components,
            "pattern_cohorts": total_cohorts,
            "pattern_themes": total_themes,
            "pattern_correlations": total_corrs,
            "high_confidence_cohorts": high_conf_cohorts,
        },
        "nodes": nodes,
        "edges": edges,
        "top_cohorts": top_cohorts,
    }


@app.get('/admin/hive/inspect', summary='Inspect the hive: cohort + correlation + theme counts (admin only)')
async def admin_hive_inspect(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Read-only snapshot for verifying Phase 1+ output without firing anything.

    Returns counts plus the top cohorts and correlations so you can eyeball
    quality before deciding whether to wire RAG into the Oracle.
    """
    from sqlalchemy import func as _func, select
    from db.database import (
        ChartSignal, ChartComponent, PatternCohort, PatternTheme,
        PatternCorrelation, UserResonance, User,
    )
    consenting = (await db.execute(
        select(_func.count(User.id)).where(User.hive_consent == True)  # noqa: E712
    )).scalar() or 0
    total_signals = (await db.execute(select(_func.count(ChartSignal.signal_id)))).scalar() or 0
    total_components = (await db.execute(select(_func.count(ChartComponent.component_id)))).scalar() or 0
    total_cohorts = (await db.execute(select(_func.count(PatternCohort.cohort_id)))).scalar() or 0
    total_themes = (await db.execute(select(_func.count(PatternTheme.theme_id)))).scalar() or 0
    total_corrs = (await db.execute(select(_func.count(PatternCorrelation.correlation_id)))).scalar() or 0
    total_resonance = (await db.execute(select(_func.count(UserResonance.user_id)))).scalar() or 0

    top_cohorts = (await db.execute(
        select(PatternCohort)
        .order_by(PatternCohort.member_count.desc())
        .limit(10)
    )).scalars().all()
    top_corrs = (await db.execute(
        select(PatternCorrelation)
        .order_by(PatternCorrelation.correlation_strength.desc())
        .limit(10)
    )).scalars().all()

    return {
        'counts': {
            'consenting_users': consenting,
            'chart_signals': total_signals,
            'chart_components': total_components,
            'pattern_cohorts': total_cohorts,
            'pattern_themes': total_themes,
            'pattern_correlations': total_corrs,
            'user_resonance_rows': total_resonance,
        },
        'top_cohorts': [
            {
                'name': c.cohort_name,
                'member_count': c.member_count,
                'confidence': c.confidence_score,
            } for c in top_cohorts
        ],
        'top_correlations': [
            {
                'a': c.component_a,
                'b': c.component_b,
                'strength': c.correlation_strength,
                'co_occurrence': c.co_occurrence_count,
            } for c in top_corrs
        ],
    }


# ---------------------------------------------------------------------------
# Akashic Record foundation: consent backfill admin endpoint
# ---------------------------------------------------------------------------
# One-shot helper to migrate every existing user's legacy hive_consent
# boolean into ConsentGrant rows for the eight-scope tiered model.
# Idempotent: re-running is safe (the unique constraint on consent_grants
# silently swallows duplicates). Manual trigger only; no scheduled call.

@app.post('/admin/akashic/backfill-consent', summary='Migrate legacy hive_consent into tiered ConsentGrant rows (admin only)')
async def admin_akashic_backfill_consent(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Mirror legacy users.hive_consent into the new ConsentGrant table.

    For each existing user:
      - hive_consent=True -> grants for: private_oracle_use,
        personalization_memory, anonymous_cohort_learning,
        anonymous_retrieval_training, anonymous_product_analytics
      - hive_consent=False -> grants for: private_oracle_use,
        personalization_memory only

    Returns counts so the dashboard can verify the migration. Safe to
    re-run; existing grants are not duplicated.
    """
    from db.database import backfill_legacy_consent
    result = await backfill_legacy_consent(db)
    logger.info(f"[akashic] consent backfill complete: {result}")
    return result


# ---------------------------------------------------------------------------
# Oracle voice audit endpoints
# ---------------------------------------------------------------------------
# Powered by oracle_audit rows written by ai/audit.audit_oracle_reply on
# every Oracle response. The dashboard uses these to render the rolling
# 7-day voice-quality picture: score distribution, top recurring
# violations, recent low-scoring chats. Admin-only.
# ---------------------------------------------------------------------------

@app.get('/admin/oracle-audit/summary', summary='Rolling 7-day Oracle voice-audit summary (admin only)')
async def admin_oracle_audit_summary(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Score distribution and top violations over the last 7 days.

    Returns:
      total_audited:    int, count of audit rows in window
      avg_score:        float, mean score in window
      median_score:     int, median score in window
      score_buckets:    dict, count per [0-39, 40-59, 60-79, 80-99, 100]
      top_violations:   list of {tag, count}, sorted descending
      by_day:           list of {date, count, avg_score} for sparkline
      by_prompt_version: dict, {version: avg_score} so we can see whether
                        the latest prompt version is moving the score
    """
    from sqlalchemy import func as _func, select
    from db.database import OracleAudit
    from datetime import timedelta
    import json as _json

    cutoff = datetime.utcnow() - timedelta(days=7)

    rows = (await db.execute(
        select(OracleAudit).where(OracleAudit.created_at >= cutoff)
    )).scalars().all()

    total = len(rows)
    if total == 0:
        return {
            'total_audited': 0,
            'avg_score': None,
            'median_score': None,
            'score_buckets': {'0-39': 0, '40-59': 0, '60-79': 0, '80-99': 0, '100': 0},
            'top_violations': [],
            'by_day': [],
            'by_prompt_version': {},
            'window_days': 7,
        }

    scores = sorted(r.score for r in rows)
    avg = sum(scores) / total
    mid = total // 2
    median = scores[mid] if total % 2 == 1 else (scores[mid - 1] + scores[mid]) // 2

    buckets = {'0-39': 0, '40-59': 0, '60-79': 0, '80-99': 0, '100': 0}
    for s in scores:
        if s == 100:           buckets['100'] += 1
        elif s >= 80:          buckets['80-99'] += 1
        elif s >= 60:          buckets['60-79'] += 1
        elif s >= 40:          buckets['40-59'] += 1
        else:                  buckets['0-39'] += 1

    # Violation tally — flatten violations_json across all rows.
    violation_counts: dict[str, int] = {}
    for r in rows:
        try:
            tags = _json.loads(r.violations_json or '[]')
        except Exception:
            tags = []
        for t in tags:
            if isinstance(t, str):
                violation_counts[t] = violation_counts.get(t, 0) + 1
    top_violations = sorted(
        [{'tag': k, 'count': v} for k, v in violation_counts.items()],
        key=lambda x: x['count'], reverse=True,
    )

    # Per-day breakdown for the sparkline. Bucket by UTC date.
    by_day_map: dict[str, dict] = {}
    for r in rows:
        d = r.created_at.date().isoformat()
        slot = by_day_map.setdefault(d, {'date': d, 'count': 0, 'sum': 0})
        slot['count'] += 1
        slot['sum'] += r.score
    by_day = sorted([
        {'date': v['date'], 'count': v['count'], 'avg_score': round(v['sum'] / v['count'], 1)}
        for v in by_day_map.values()
    ], key=lambda x: x['date'])

    # Per-prompt-version average — useful when a prompt change ships,
    # to see whether scores moved up or down vs. the prior version.
    by_pv_map: dict[str, dict] = {}
    for r in rows:
        pv = r.oracle_prompt_version or 'unknown'
        slot = by_pv_map.setdefault(pv, {'count': 0, 'sum': 0})
        slot['count'] += 1
        slot['sum'] += r.score
    by_pv = {
        pv: {'count': v['count'], 'avg_score': round(v['sum'] / v['count'], 1)}
        for pv, v in by_pv_map.items()
    }

    return {
        'total_audited': total,
        'avg_score': round(avg, 1),
        'median_score': median,
        'score_buckets': buckets,
        'top_violations': top_violations,
        'by_day': by_day,
        'by_prompt_version': by_pv,
        'window_days': 7,
    }


@app.get('/admin/oracle-audit/lowest', summary='Recent lowest-scoring Oracle replies (admin only)')
async def admin_oracle_audit_lowest(
    limit: int = 20,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """The N lowest-scoring chats in the last 7 days, with full reply text.

    Use this to read what the auditor actually flagged. Each row shows the
    user message, the Oracle reply, the score, the violations list, and
    the auditor's one-sentence note. Reading 5-10 of these after a prompt
    change is the fastest way to verify the change actually moved
    the right needle.
    """
    from sqlalchemy import select
    from db.database import OracleAudit
    from datetime import timedelta
    import json as _json

    if limit < 1 or limit > 100:
        limit = 20
    cutoff = datetime.utcnow() - timedelta(days=7)

    rows = (await db.execute(
        select(OracleAudit)
        .where(OracleAudit.created_at >= cutoff)
        .order_by(OracleAudit.score.asc(), OracleAudit.created_at.desc())
        .limit(limit)
    )).scalars().all()

    items = []
    for r in rows:
        try:
            tags = _json.loads(r.violations_json or '[]')
        except Exception:
            tags = []
        items.append({
            'id': r.id,
            'created_at': r.created_at.isoformat(),
            'user_id': r.user_id,
            'score': r.score,
            'violations': tags,
            'notes': r.notes,
            'user_message_excerpt': r.user_message_excerpt,
            'reply_excerpt': r.reply_excerpt,
            'model_used': r.model_used,
            'oracle_prompt_version': r.oracle_prompt_version,
        })
    return {'items': items, 'count': len(items)}


# ---------------------------------------------------------------------------
# Marketing tool: metrics
# ---------------------------------------------------------------------------

@app.get('/admin/metrics', summary='Marketing dashboard metrics (admin only)')
async def admin_metrics(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Live counts and money for the marketing dashboard.

    Numbers come from the production users table; nothing is faked. If a
    field has no data yet the response carries a null, not zero, so the
    UI can render an honest 'not yet' state instead of a misleading zero.
    """
    from sqlalchemy import text, select, func
    from datetime import datetime, timedelta

    # Subscriber + revenue metrics from the subscriptions / users tables.
    PRICE_USD_MONTHLY = 23.0

    out: dict = {
        'price_usd_monthly': PRICE_USD_MONTHLY,
        'generated_at': datetime.utcnow().isoformat(),
    }

    try:
        result = await db.execute(text("SELECT COUNT(*) FROM users"))
        out['total_users'] = result.scalar() or 0
    except Exception:
        out['total_users'] = None

    try:
        # Trialing users — have an active trial that hasn't expired and they
        # have not yet converted to paid.
        result = await db.execute(text(
            "SELECT COUNT(*) FROM users u "
            "WHERE EXISTS ("
            "  SELECT 1 FROM subscriptions s "
            "  WHERE s.user_id = u.id AND s.status = 'trialing'"
            ")"
        ))
        out['trial_users'] = result.scalar() or 0
    except Exception:
        out['trial_users'] = None

    try:
        result = await db.execute(text(
            "SELECT COUNT(*) FROM subscriptions WHERE status = 'active'"
        ))
        out['paying_subscribers'] = result.scalar() or 0
    except Exception:
        out['paying_subscribers'] = None

    if out.get('paying_subscribers') is not None:
        out['mrr_usd'] = round(out['paying_subscribers'] * PRICE_USD_MONTHLY, 2)
    else:
        out['mrr_usd'] = None

    try:
        result = await db.execute(text(
            "SELECT COUNT(*) FROM users WHERE created_at > NOW() - INTERVAL '7 days'"
        ))
        out['signups_last_7d'] = result.scalar() or 0
    except Exception:
        out['signups_last_7d'] = None

    try:
        result = await db.execute(text(
            "SELECT COUNT(*) FROM users WHERE created_at > NOW() - INTERVAL '30 days'"
        ))
        out['signups_last_30d'] = result.scalar() or 0
    except Exception:
        out['signups_last_30d'] = None

    # Active users — anyone whose forecast was generated in the last 7 days
    # (the closest signal we have for "opened the app today/recently").
    try:
        result = await db.execute(text(
            "SELECT COUNT(DISTINCT user_id) FROM daily_forecasts "
            "WHERE created_at > NOW() - INTERVAL '7 days'"
        ))
        out['active_users_7d'] = result.scalar() or 0
    except Exception:
        out['active_users_7d'] = None

    return out


# ---------------------------------------------------------------------------
# Marketing tool: calendar events CRUD
# ---------------------------------------------------------------------------

class MarketingEventCreate(BaseModel):
    title:         str
    channel:       str
    scheduled_for: str  # ISO 8601
    content_draft: Optional[str] = None
    asset_notes:   Optional[str] = None
    status:        Optional[str] = 'idea'


class MarketingEventUpdate(BaseModel):
    title:         Optional[str] = None
    channel:       Optional[str] = None
    scheduled_for: Optional[str] = None
    content_draft: Optional[str] = None
    asset_notes:   Optional[str] = None
    status:        Optional[str] = None


def _serialize_event(e: MarketingEvent) -> dict:
    return {
        'id':            e.id,
        'title':         e.title,
        'channel':       e.channel,
        'scheduled_for': e.scheduled_for.isoformat() if e.scheduled_for else None,
        'content_draft': e.content_draft,
        'asset_notes':   e.asset_notes,
        'status':        e.status,
        'created_at':    e.created_at.isoformat() if e.created_at else None,
        'updated_at':    e.updated_at.isoformat() if e.updated_at else None,
    }


@app.get('/admin/marketing/events', summary='List marketing calendar events (admin only)')
async def list_marketing_events(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(
        select(MarketingEvent).order_by(MarketingEvent.scheduled_for.asc())
    )
    events = result.scalars().all()
    return {'events': [_serialize_event(e) for e in events]}


@app.post('/admin/marketing/events', summary='Create a marketing calendar event (admin only)', status_code=201)
async def create_marketing_event(
    req: MarketingEventCreate,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        scheduled = datetime.fromisoformat(req.scheduled_for.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail='scheduled_for must be ISO 8601')

    event = MarketingEvent(
        id=str(uuid.uuid4()),
        title=req.title.strip(),
        channel=req.channel.strip(),
        scheduled_for=scheduled,
        content_draft=req.content_draft,
        asset_notes=req.asset_notes,
        status=(req.status or 'idea').strip(),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return _serialize_event(event)


@app.patch('/admin/marketing/events/{event_id}', summary='Update a marketing event (admin only)')
async def update_marketing_event(
    event_id: str,
    req: MarketingEventUpdate,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(select(MarketingEvent).where(MarketingEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')

    if req.title is not None:         event.title = req.title.strip()
    if req.channel is not None:       event.channel = req.channel.strip()
    if req.content_draft is not None: event.content_draft = req.content_draft
    if req.asset_notes is not None:   event.asset_notes = req.asset_notes
    if req.status is not None:        event.status = req.status.strip()
    if req.scheduled_for is not None:
        try:
            event.scheduled_for = datetime.fromisoformat(req.scheduled_for.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail='scheduled_for must be ISO 8601')

    await db.commit()
    await db.refresh(event)
    return _serialize_event(event)


@app.delete('/admin/marketing/events/{event_id}', summary='Delete a marketing event (admin only)')
async def delete_marketing_event(
    event_id: str,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(select(MarketingEvent).where(MarketingEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')
    await db.delete(event)
    await db.commit()
    return {'ok': True, 'deleted': event_id}


# ---------------------------------------------------------------------------
# Marketing tool: integration connection status
# ---------------------------------------------------------------------------

# Catalogue of integrations the marketing tool knows about. Each entry is
# the canonical metadata the UI uses to render the connection card.
# Status comes from the integration_credentials table; everything else is
# constant. Keep this list in sync with the frontend integration cards.
_INTEGRATION_CATALOGUE = [
    {
        'kind': 'meta_ads',
        'name': 'Meta Ads',
        'category': 'ads',
        'description': 'Read ad performance, surface what is working, and recommend next steps.',
        'prerequisites': [
            'Meta Business Manager account',
            'Meta ad account with at least one campaign',
            'Marketing API access (Meta reviews, 1-7 days)',
            'Facebook Pixel installed on solray.ai',
            'OAuth approval for ads_read + ads_management',
        ],
    },
    {
        'kind': 'x',
        'name': 'X (Twitter)',
        'category': 'social',
        'description': 'Post drafts, read engagement, surface trends in the astrology conversation.',
        'prerequisites': [
            'X Developer account',
            'API key + secret + bearer token',
            'OAuth 2.0 user context for posting',
        ],
    },
    {
        'kind': 'instagram',
        'name': 'Instagram',
        'category': 'social',
        'description': 'Cross-post share cards, read engagement, schedule stories.',
        'prerequisites': [
            'Instagram Business or Creator account',
            'Linked Facebook Page',
            'Instagram Graph API access via Meta Business Manager',
        ],
    },
    {
        'kind': 'tiktok',
        'name': 'TikTok',
        'category': 'social',
        'description': 'Schedule short-form video posts, pull view and engagement counts.',
        'prerequisites': [
            'TikTok for Business account',
            'TikTok Developer app with Content Posting + Login Kit',
            'OAuth approval',
        ],
    },
    {
        'kind': 'linkedin',
        'name': 'LinkedIn',
        'category': 'social',
        'description': 'Post longer-form pieces and read company-page analytics.',
        'prerequisites': [
            'LinkedIn Company Page (admin role)',
            'LinkedIn Developer app with w_member_social + r_organization_social',
        ],
    },
    {
        'kind': 'vercel_analytics',
        'name': 'Vercel Analytics',
        'category': 'analytics',
        'description': 'Live page-view, country, and referrer data for solray.ai and app.solray.ai.',
        'prerequisites': [
            'Enable Web Analytics on both Vercel projects',
            'Vercel API token with read scope on the team',
        ],
    },
    {
        'kind': 'posthog',
        'name': 'PostHog',
        'category': 'analytics',
        'description': 'Funnel analysis: visit to signup to subscription, plus session replay if enabled.',
        'prerequisites': [
            'PostHog cloud project',
            'Project API key',
            'Event taxonomy decided (signup, trial_start, subscribe, etc.)',
        ],
    },
]


@app.get('/admin/integrations', summary='List marketing integrations + their connection status (admin only)')
async def list_integrations(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(select(IntegrationCredential))
    by_kind = {row.kind: row for row in result.scalars().all()}

    out = []
    for entry in _INTEGRATION_CATALOGUE:
        record = by_kind.get(entry['kind'])
        out.append({
            **entry,
            'status':       record.status if record else 'not_connected',
            'last_synced':  record.last_synced.isoformat() if record and record.last_synced else None,
            'last_error':   record.last_error if record else None,
        })
    return {'integrations': out}


# ---------------------------------------------------------------------------
# Marketing tool: Signal Radar
# ---------------------------------------------------------------------------

class SignalCreate(BaseModel):
    title:   str
    body:    Optional[str] = None
    url:     Optional[str] = None
    source:  Optional[str] = 'manual'
    score:   Optional[int] = 50
    happens_at: Optional[str] = None  # ISO


class SignalUpdate(BaseModel):
    title:  Optional[str] = None
    body:   Optional[str] = None
    url:    Optional[str] = None
    score:  Optional[int] = None
    status: Optional[str] = None  # active | dismissed | acted


def _serialize_signal(s: MarketingSignal) -> dict:
    angles = None
    if s.angles_json:
        try:
            import json
            angles = json.loads(s.angles_json)
        except Exception:
            angles = None
    return {
        'id':         s.id,
        'source':     s.source,
        'title':      s.title,
        'body':       s.body,
        'url':        s.url,
        'score':      s.score,
        'status':     s.status,
        'angles':     angles,
        'happens_at': s.happens_at.isoformat() if s.happens_at else None,
        'created_at': s.created_at.isoformat() if s.created_at else None,
        'updated_at': s.updated_at.isoformat() if s.updated_at else None,
    }


@app.get('/admin/marketing/signals', summary='List Signal Radar signals (admin only)')
async def list_signals(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(
        select(MarketingSignal)
        .where(MarketingSignal.status != 'dismissed')
        .order_by(MarketingSignal.score.desc(), MarketingSignal.created_at.desc())
    )
    signals = result.scalars().all()
    return {'signals': [_serialize_signal(s) for s in signals]}


@app.post('/admin/marketing/signals', summary='Create a signal (admin only)', status_code=201)
async def create_signal(
    req: SignalCreate,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    happens = None
    if req.happens_at:
        try:
            happens = datetime.fromisoformat(req.happens_at.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail='happens_at must be ISO 8601')

    signal = MarketingSignal(
        id=str(uuid.uuid4()),
        source=(req.source or 'manual').strip(),
        title=req.title.strip(),
        body=req.body,
        url=req.url,
        score=req.score if req.score is not None else 50,
        happens_at=happens,
    )
    db.add(signal)
    await db.commit()
    await db.refresh(signal)
    return _serialize_signal(signal)


@app.patch('/admin/marketing/signals/{signal_id}', summary='Update a signal (admin only)')
async def update_signal(
    signal_id: str,
    req: SignalUpdate,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(select(MarketingSignal).where(MarketingSignal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail='Signal not found')

    if req.title is not None:  signal.title = req.title.strip()
    if req.body is not None:   signal.body = req.body
    if req.url is not None:    signal.url = req.url
    if req.score is not None:  signal.score = max(0, min(100, int(req.score)))
    if req.status is not None: signal.status = req.status.strip()

    await db.commit()
    await db.refresh(signal)
    return _serialize_signal(signal)


@app.delete('/admin/marketing/signals/{signal_id}', summary='Delete a signal (admin only)')
async def delete_signal(
    signal_id: str,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(select(MarketingSignal).where(MarketingSignal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail='Signal not found')
    await db.delete(signal)
    await db.commit()
    return {'ok': True, 'deleted': signal_id}


@app.post('/admin/marketing/signals/{signal_id}/angles', summary='Generate Solray angles for a signal (admin only)')
async def generate_signal_angles(
    signal_id: str,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Call the Anthropic Haiku model to generate up to 5 ranked Solray
    angles for the signal. Persists them on the row so the UI can read
    them back without paying for tokens twice.
    """
    from sqlalchemy import select
    result = await db.execute(select(MarketingSignal).where(MarketingSignal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail='Signal not found')

    from marketing.ai import generate_angles_for_signal
    angles = generate_angles_for_signal(signal.title, signal.body, signal.source)

    if not angles:
        raise HTTPException(status_code=502, detail='AI did not return any angles. Try again.')

    import json
    signal.angles_json = json.dumps(angles)
    await db.commit()
    await db.refresh(signal)
    return _serialize_signal(signal)


# ---------------------------------------------------------------------------
# Marketing tool: Founder Voice Studio
# ---------------------------------------------------------------------------

class VoiceStudioRequest(BaseModel):
    raw_note: str
    channels: Optional[List[str]] = None


@app.post('/admin/marketing/voice', summary='Convert raw note to per-platform Solray drafts (admin only)')
async def voice_studio(
    req: VoiceStudioRequest,
    admin_id: str = Depends(require_admin),
):
    if not req.raw_note or not req.raw_note.strip():
        raise HTTPException(status_code=400, detail='raw_note is required')
    from marketing.ai import generate_platform_variants
    variants = generate_platform_variants(req.raw_note, req.channels)
    if not variants:
        raise HTTPException(status_code=502, detail='AI did not return any variants. Try again.')
    return {'variants': variants}


# ---------------------------------------------------------------------------
# Marketing tool: brand-rule linter (no AI, instant)
# ---------------------------------------------------------------------------

class LintRequest(BaseModel):
    text: str


@app.post('/admin/marketing/lint', summary='Lint draft copy against Solray brand rules (admin only)')
async def brand_lint(
    req: LintRequest,
    admin_id: str = Depends(require_admin),
):
    from marketing.brand_lint import lint
    return {'violations': lint(req.text or '')}


# ---------------------------------------------------------------------------
# Marketing tool: upcoming astro events
# ---------------------------------------------------------------------------

@app.get('/admin/marketing/astro-events', summary='Upcoming sky events for marketing windows (admin only)')
async def astro_events(
    admin_id: str = Depends(require_admin),
    days: int = 60,
):
    """Returns Mercury retrogrades, ingresses, and lunar quarters for the
    next `days` days. The marketing calendar overlays these so Bob can
    see when a transit-shaped post would land hardest.
    """
    from marketing.astro_events import upcoming_events
    days = max(7, min(180, int(days)))
    return {'events': upcoming_events(days=days)}


# Score weights by sky-event kind for seeding into Signal Radar.
_SKY_KIND_SCORE = {
    'station_retrograde': 85,
    'station_direct':     70,
    'ingress':            65,
    'lunar_phase':        55,
}


@app.post('/admin/marketing/seed-from-sky', summary='Seed Signal Radar with upcoming sky events (admin only)')
async def seed_signals_from_sky(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = 60,
):
    """Idempotent. Every upcoming sky event becomes a MarketingSignal with
    source='astro_event'. Skips events that already have a matching
    signal (same source + title + happens_at). Safe to re-run weekly as
    the horizon rolls forward. Returns counts.
    """
    from sqlalchemy import select
    from marketing.astro_events import upcoming_events

    days = max(7, min(180, int(days)))
    sky = upcoming_events(days=days)

    inserted = 0
    skipped = 0
    for ev in sky:
        try:
            happens = datetime.fromisoformat(ev['happens_at'].replace('Z', '+00:00'))
        except Exception:
            continue

        existing = await db.execute(
            select(MarketingSignal).where(
                MarketingSignal.source == 'astro_event',
                MarketingSignal.title == ev['label'],
                MarketingSignal.happens_at == happens,
            )
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue

        signal = MarketingSignal(
            id=str(uuid.uuid4()),
            source='astro_event',
            title=ev['label'],
            body=f"Kind: {ev.get('kind', 'sky_event')}",
            url=None,
            score=_SKY_KIND_SCORE.get(ev.get('kind'), 60),
            happens_at=happens,
        )
        db.add(signal)
        inserted += 1

    if inserted > 0:
        await db.commit()

    return {
        'days': days,
        'sky_events_seen': len(sky),
        'signals_inserted': inserted,
        'signals_skipped':  skipped,
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get('/', summary='Health check')
async def root():
    return {
        'status': 'ok',
        'service': 'Solray AI API',
        'version': '1.0.0',
        'docs': '/docs',
    }


@app.get('/dashboard', summary='Mission Control dashboard stats')
async def dashboard_stats(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    from datetime import datetime, timedelta

    # User stats
    result = await db.execute(text("SELECT COUNT(*) FROM users"))
    total_users = result.scalar()

    result = await db.execute(text("SELECT COUNT(*) FROM users WHERE created_at > NOW() - INTERVAL '7 days'"))
    new_users_7d = result.scalar()

    try:
        result = await db.execute(text("SELECT COUNT(*) FROM waitlist"))
        total_waitlist = result.scalar()

        result = await db.execute(text("SELECT COUNT(*) FROM waitlist WHERE created_at > NOW() - INTERVAL '7 days'"))
        new_waitlist_7d = result.scalar()
    except Exception:
        total_waitlist = 0
        new_waitlist_7d = 0

    return {
        "users": {"total": total_users, "new_7d": new_users_7d},
        "waitlist": {"total": total_waitlist, "new_7d": new_waitlist_7d},
        "backend_version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }



@app.post('/admin/recalculate/{email}', summary="Recalculate blueprint for a user (admin only)")
async def recalculate_blueprint(
    email: str,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Recalculate and overwrite the stored blueprint for a user."""
    user = await get_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    from astrology import geocode_city
    try:
        lat, lon = geocode_city(user.birth_city)
    except Exception:
        lat, lon = user.birth_lat, user.birth_lon
    tz_offset = get_tz_offset(lat, lon, user.birth_date, user.birth_time)
    blueprint = engines.build_blueprint(
        birth_date=user.birth_date,
        birth_time=user.birth_time,
        birth_city=user.birth_city,
        birth_lat=lat,
        birth_lon=lon,
        tz_offset=tz_offset,
    )
    await upsert_blueprint(db, user.id, blueprint)
    hd = blueprint.get('human_design', {})
    return {
        'recalculated': email,
        'type': hd.get('type'),
        'profile': hd.get('profile'),
        'authority': hd.get('authority'),
    }


@app.post('/admin/recalculate-all', summary="Recalculate blueprints for every user (admin only)")
async def recalculate_all_blueprints(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Recalculate and overwrite the stored blueprint for every user in the database.
    Use after a calculation engine fix to flush stale cached blueprints.
    """
    from sqlalchemy import select as sa_select
    results = []
    users_result = await db.execute(sa_select(User))
    users = users_result.scalars().all()
    for user in users:
        try:
            from astrology import geocode_city
            try:
                lat, lon = geocode_city(user.birth_city)
            except Exception:
                lat, lon = user.birth_lat, user.birth_lon
            tz_offset = get_tz_offset(lat, lon, user.birth_date, user.birth_time)
            blueprint = engines.build_blueprint(
                birth_date=user.birth_date,
                birth_time=user.birth_time,
                birth_city=user.birth_city,
                birth_lat=lat,
                birth_lon=lon,
                tz_offset=tz_offset,
            )
            await upsert_blueprint(db, user.id, blueprint)
            hd = blueprint.get('human_design', {})
            results.append({
                'email': user.email,
                'name': user.name,
                'type': hd.get('type'),
                'profile': hd.get('profile'),
                'incarnation_cross': blueprint.get('summary', {}).get('incarnation_cross'),
                'status': 'ok',
            })
        except Exception as e:
            results.append({'email': user.email, 'status': 'error', 'error': str(e)})
    return {'recalculated': len(results), 'results': results}


@app.get('/astrocartography', summary="Get astrocartography lines for the authenticated user")
async def astrocartography(
    user_id: str = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns astrocartography (astrogeography) lines for the user's natal chart.
    Each line shows where a planet was on the MC, IC, ASC, or DSC at birth.
    Results are cached in the blueprint and rarely change.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')

    try:
        from astrocartography import calc_astrocartography, get_line_meaning
        tz_offset = get_tz_offset(user.birth_lat, user.birth_lon, user.birth_date, user.birth_time)
        result = calc_astrocartography(
            birth_date=user.birth_date,
            birth_time=user.birth_time,
            birth_lat=user.birth_lat,
            birth_lon=user.birth_lon,
            tz_offset=tz_offset,
            lat_step=5.0,
        )
        # Add interpretations to each line
        for line in result['lines']:
            line['meaning'] = get_line_meaning(line['planet'], line['type'])
        return result
    except Exception as e:
        logger.exception(f"Astrocartography calculation failed for user {user_id}")
        raise HTTPException(status_code=500, detail=f'Astrocartography calculation failed: {str(e)}')



# ---------------------------------------------------------------------------
# GET /transits/long-range
# ---------------------------------------------------------------------------

@app.get('/transits/long-range', summary="Get the user's active long-range astrological cycles")
async def long_range_transits(
    user_id: str = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns major multi-month/multi-year astrological cycles currently active for the user.

    Includes:
      - Saturn Return (transit Saturn conjunct natal Saturn, orb 10°)
      - Jupiter Return (transit Jupiter conjunct natal Jupiter, orb 8°)
      - Nodal Return (transit North Node conjunct natal North Node, orb 8°)
      - Outer planet transits (Pluto/Neptune/Uranus/Saturn/Jupiter) over natal Sun/Moon/Ascendant

    Each active transit includes title, summary, start/peak/end dates, orb, and phase.
    Results are suitable for monthly caching on the client side.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')

    blueprint = await get_blueprint(db, user_id)
    if not blueprint:
        raise HTTPException(status_code=404, detail='Blueprint not found. Please regenerate.')

    try:
        from long_range import calc_long_range_transits, get_upcoming_cycles
        transits = calc_long_range_transits(blueprint)
        upcoming = get_upcoming_cycles(blueprint)
    except Exception as e:
        logger.exception(f"Long-range transit calculation failed for user {user_id}")
        try:
            import sentry_sdk as _sentry
            _sentry.capture_exception(e)
        except Exception:
            logger.warning("Sentry not available for error reporting")
        raise HTTPException(status_code=500, detail=f'Long-range transit calculation failed: {str(e)}')

    # Generate AI summaries if we have transits
    if transits:
        try:
            from ai.long_range_ai import generate_transit_summaries
            transits = generate_transit_summaries(transits, blueprint)
        except Exception as e:
            # AI summaries failed — return transits without summaries
            logger.warning(f"AI summary generation failed for user {user_id}: {e}")
            try:
                import sentry_sdk as _sentry
                _sentry.capture_exception(e)
            except Exception:
                logger.warning("Sentry not available for error reporting")

    return {
        'cycles':         transits,
        'upcoming':       upcoming,
        'total_active':   len(transits),
        'total_upcoming': len(upcoming),
        # legacy field kept for backward compatibility
        'count':          len(transits),
        'generated_at':   datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

@app.post('/chat', summary='Chat with your Higher Self')
async def chat_endpoint(
    req: ChatRequest,
    user_id: str = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    # Generate request_uuid early so log_api_usage and log_chat_lineage can
    # correlate to the same logical request. Capture start_time for latency.
    import uuid as _uuid_mod
    import time as _time_mod
    _request_uuid = str(_uuid_mod.uuid4())
    _chat_start_t = _time_mod.monotonic()

    blueprint = await get_blueprint(db, user_id)
    if not blueprint:
        raise HTTPException(status_code=404, detail='Blueprint not found')
    # Inject user identity (name, sex) into blueprint.meta so the Oracle
    # addresses each user in their own name and stated pronouns. Sex is
    # captured at signup; legacy users without it fall back to feminine
    # defaults in the prompt builder (preserves prior behavior).
    if 'meta' not in blueprint:
        blueprint['meta'] = {}
    blueprint['meta'].setdefault('name', user.name)
    blueprint['meta']['sex'] = getattr(user, 'sex', None)
    from datetime import date
    today_str = date.today().isoformat()
    forecast = await get_cached_forecast(db, user_id, today_str)
    # If the user walks straight into chat without loading the Today tab first,
    # the forecast cache is empty and the Oracle has no transits to speak from.
    # Compute the raw ephemeris snapshot on demand so "what planets are in X
    # right now" always has a real answer. This is fast, no AI call, pure swisseph.
    if not forecast:
        try:
            forecast = engines.get_daily_forecast(
                birth_date=user.birth_date,
                birth_time=user.birth_time,
                birth_city=user.birth_city,
                birth_lat=user.birth_lat,
                birth_lon=user.birth_lon,
            )
        except Exception as _fc_err:
            logger.warning(f"On-demand forecast calc failed for user {user_id}: {_fc_err}")
            forecast = None
    history = [{"role": m.role, "content": m.content} for m in req.conversation_history]

    # Load persistent user memories for continuity across sessions.
    # Order matters: load memories FIRST with surface_next flags intact,
    # let the Oracle use them in this turn, THEN clear surface_next on a
    # new session AFTER the response is generated. The previous version
    # cleared surface_next BEFORE loading memories, which meant the
    # Oracle never actually saw the flagged memories on a new session,
    # defeating the entire surface_next mechanism. Surfaced by a
    # cross-agent review (Codex) in May 2026.
    from db.database import (
        get_user_memories, update_user_memories, reset_surface_next_flags,
        delete_all_user_memories, get_accepted_connections_summary,
        get_oracle_self_state, upsert_oracle_self_state,  # noqa: F401
    )
    is_new_session = len(history) == 0
    memories = await get_user_memories(db, user_id)

    # Load the user's accepted connections (souls) with chart chips so the
    # Oracle reads them as YOUR PEOPLE in the prompt. Failure to load is
    # non-fatal: the Oracle falls back to user-only memory.
    try:
        connections = await get_accepted_connections_summary(db, user_id)
    except Exception as conn_err:
        logger.warning(f"Failed to load connections for user {user_id}: {conn_err}")
        connections = []

    # Load the Oracle's own self-state for this user — her becoming, not the
    # user's. Renders WHO YOU HAVE BECOME in the prompt. Non-fatal.
    try:
        self_state = await get_oracle_self_state(db, user_id)
    except Exception as ss_err:
        logger.warning(f"Failed to load oracle self-state for user {user_id}: {ss_err}")
        self_state = None

    # Load hive context (collective-intelligence layer). Closes the loop
    # Codex flagged in the May 2026 audit roundtable: the Hive Mind tables
    # exist but the Oracle never read from them. Failure is non-fatal,
    # the Oracle just gets the un-augmented prompt for this turn. Will
    # return empty for users without hive_consent or with no signal yet.
    try:
        from db.database import get_user_hive_context
        hive_context = await get_user_hive_context(db, user_id)
    except Exception as hv_err:
        logger.warning(f"Failed to load hive context for user {user_id}: {hv_err}")
        hive_context = None

    # Ship #1 from the 100% realism roadmap: event-grounded memory retrieval.
    # Pull zero to three past raw moments (NarrativeEvent rows) that score
    # above the relevance threshold against the current message. Pure
    # cross-session continuity: skip events from the active session because
    # we want the Oracle to recognize patterns across days/weeks, not echo
    # what was just said. Failure is non-fatal; the Oracle just gets the
    # prompt without past-moments for this turn.
    past_moments = []
    try:
        from db.database import retrieve_relevant_narrative_events
        if req.message and req.message.strip():
            current_session_id = getattr(req, 'session_id', None)
            past_moments = await retrieve_relevant_narrative_events(
                db,
                user_id=user_id,
                current_message=req.message,
                max_results=3,
                skip_session_id=current_session_id,
            )
    except Exception as nm_err:
        logger.warning(f"[narrative] retrieval failed for user {user_id}: {nm_err}")
        past_moments = []

    try:
        response = higher_self_chat(
            blueprint=blueprint,
            forecast=forecast,
            conversation_history=history,
            user_message=req.message,
            soul_blueprint=req.soul_blueprint,
            memories=memories,
            connections=connections,
            self_state=self_state,
            hive_context=hive_context,
            past_moments=past_moments,
        )

        # surface_next memories have now been "consumed" by the Oracle in
        # this response, clear them so they are not re-surfaced
        # indefinitely. Only on the first turn of a session, since that
        # is the moment continuity from the previous session matters.
        if is_new_session:
            try:
                await reset_surface_next_flags(db, user_id)
            except Exception as _flag_err:
                logger.warning(f"reset_surface_next_flags failed for user {user_id}: {_flag_err}")

        # Synthesize memories in background at session checkpoints.
        # Fire at message 2, 5, 8, 11... so even short sessions (2-3 turns,
        # the common case) still get captured. Memory between chats depends
        # on synthesis actually firing often enough to be there next time.
        # Also fire if this looks like a session-ending message (history is long
        # and message is short, suggesting a closing exchange).
        user_message_count = sum(1 for m in history if m.get('role') == 'user')
        next_count = user_message_count + 1
        should_synthesize = (
            next_count == 2
            or (next_count >= 5 and (next_count - 2) % 3 == 0)
            or (user_message_count >= 4 and req.message and len(req.message.split()) < 6)
        )
        # Self-state cadence is INDEPENDENT of memory cadence. Memory fires
        # at 2, 5, 8, 11... and closers. Self-state fires at 2, 7, 12, 17...
        # (every 5 turns starting from turn 2). This way self-reflection
        # actually runs at the intended slower-than-memory rhythm even when
        # memory synthesis is also off this turn. (Previous version gated
        # self-reflection inside should_synthesize and so only ever fired
        # at turns 2 and 5 — caught by Codex audit.)
        should_self_reflect = (next_count == 2) or (next_count >= 7 and (next_count - 2) % 5 == 0)

        if should_synthesize or should_self_reflect:
            import asyncio
            from ai.chat import synthesize_memories, synthesize_oracle_self_state
            # Append BOTH the current user message AND the response we just
            # produced, so synthesis sees the complete exchange — including
            # what the Oracle actually said this turn. Previously self-state
            # reflected before seeing her own reply, which is incoherent.
            full_history = (
                history
                + [{"role": "user", "content": req.message or ""}]
                + [{"role": "assistant", "content": response or ""}]
            )
            # Capture connections snapshot for the synthesis closure (avoids
            # re-querying inside the background task). The synthesizer uses
            # this to know which names map to which connection_user_ids when
            # tagging memories.
            connections_snapshot = list(connections) if connections else []
            self_state_snapshot = self_state
            do_memory = should_synthesize
            do_self = should_self_reflect
            async def _synthesize():
                # Memory synthesis (about the user). Skipped when only
                # self-state is due this turn.
                if do_memory:
                    try:
                        logger.info(
                            f"[memory] synthesis triggered for user {user_id} "
                            f"at turn={next_count} existing_count={len(memories)} "
                            f"connections={len(connections_snapshot)}"
                        )
                        new_memories = synthesize_memories(
                            blueprint, full_history, memories,
                            connections=connections_snapshot,
                        )
                        if new_memories:
                            await update_user_memories(db, user_id, new_memories)
                            logger.info(
                                f"[memory] persisted {len(new_memories)} memories for user {user_id}"
                            )
                        else:
                            logger.info(f"[memory] synthesis returned 0 memories for user {user_id}")
                    except Exception as err:
                        logger.exception(f"[memory] persist failed for user {user_id}: {err}")

                # Self-state pass: the Oracle reflects on HER own becoming
                # in this relationship. Independent cadence; fires at turns
                # 2, 7, 12, 17... Increment session_count once per new
                # session (history was empty when this request arrived).
                if do_self:
                    try:
                        new_state = synthesize_oracle_self_state(
                            blueprint, full_history, self_state_snapshot,
                        )
                        if new_state or is_new_session:
                            await upsert_oracle_self_state(
                                db, user_id,
                                own_arc=(new_state or {}).get('own_arc'),
                                voice_calibration=(new_state or {}).get('voice_calibration'),
                                self_observations=(new_state or {}).get('self_observations'),
                                increment_session=is_new_session,
                            )
                            logger.info(
                                f"[self_state] updated for user {user_id} "
                                f"fields={list((new_state or {}).keys())} "
                                f"new_session={is_new_session}"
                            )
                    except Exception as err:
                        logger.exception(f"[self_state] update failed for user {user_id}: {err}")
            asyncio.create_task(_synthesize())

        # Voice-consistency audit. Fire-and-forget on every reply. Runs a
        # GPT-4o pass against the Oracle's voice rules and persists the
        # score + flagged violations. Zero impact on user latency: the
        # response is already on its way back to the user. Logs and dies
        # silently on any failure (no key, OpenAI down, etc.) so it can
        # never break the chat path. Surfaces in /admin/oracle-audit
        # endpoints + the Oracle Voice Health section on /admin/hive.
        # Uses _spawn_background so the Task is held by a strong ref and
        # cannot be garbage-collected mid-flight (Codex audit).
        # Provenance: read which model produced the reply via chat.py's
        # LAST_MODEL_USED ContextVar, so the audit can skip break-glass
        # output (Gemini caught: do not let GPT-4o grade GPT-4o).
        try:
            from ai.audit import audit_oracle_reply
            from ai.chat import LAST_MODEL_USED, get_oracle_prompt_version
            _spawn_background(audit_oracle_reply(
                user_id=user_id,
                user_message=req.message,
                oracle_reply=response or "",
                model_used=LAST_MODEL_USED.get(),
                oracle_prompt_version=get_oracle_prompt_version(),
                blueprint=blueprint,
            ))
        except Exception as _audit_err:
            logger.warning(f"[audit] schedule failed for user {user_id}: {_audit_err}")

        # Ship #1 write side: persist one NarrativeEvent for the user message
        # and one for the Oracle reply. These rows are what future turns will
        # retrieve from. Fire-and-forget so the user does not wait. Uses a
        # FRESH AsyncSession inside the background task because the outer
        # `db` session is closed when the request returns.
        try:
            from db.database import add_narrative_event, AsyncSessionLocal
            from ai.chat import LAST_MODEL_USED, get_oracle_prompt_version
            current_session_id = getattr(req, 'session_id', None)
            user_text = req.message or ""
            oracle_text = response or ""
            model_used_now = LAST_MODEL_USED.get()
            prompt_version_now = get_oracle_prompt_version()

            async def _write_narrative_events():
                try:
                    async with AsyncSessionLocal() as fresh_db:
                        if user_text.strip():
                            await add_narrative_event(
                                fresh_db,
                                user_id=user_id,
                                role='user',
                                content=user_text,
                                chat_session_id=current_session_id,
                                origin_surface='chat',
                            )
                        if oracle_text.strip():
                            await add_narrative_event(
                                fresh_db,
                                user_id=user_id,
                                role='oracle',
                                content=oracle_text,
                                chat_session_id=current_session_id,
                                origin_surface='chat',
                                extraction_model=model_used_now,
                                extraction_prompt_version=prompt_version_now,
                            )
                except Exception as nev_err:
                    logger.warning(f"[narrative] write failed for user {user_id}: {nev_err}")
            _spawn_background(_write_narrative_events())
        except Exception as _ne_err:
            logger.warning(f"[narrative] schedule failed for user {user_id}: {_ne_err}")

        # Hub slice 2: lineage write. Append-only provenance trail of this
        # request. Fire-and-forget so it never blocks the user response.
        try:
            from db.database import log_chat_lineage
            from ai.chat import ORACLE_PROMPT_TAG as _opt
            _memory_ids = [str(m.get('id')) for m in (memories or []) if isinstance(m, dict) and m.get('id')]
            _event_ids = [str(e.get('id')) for e in (past_moments or []) if isinstance(e, dict) and e.get('id')]
            async def _write_lineage():
                try:
                    await log_chat_lineage(
                        request_uuid=_request_uuid,
                        user_id=user_id,
                        session_id=current_session_id if 'current_session_id' in locals() else None,
                        prompt_hash=None,  # filled in once prompt_hash is exposed by chat()
                        system_prompt_version=_opt,
                        retrieval_version="v1",
                        model=None,  # filled by api_usage rows
                        is_break_glass=False,
                        retrieved_memory_ids=_memory_ids,
                        retrieved_event_ids=_event_ids,
                        hive_context_used=bool(hive_context),
                        user_message_excerpt=req.message or "",
                        reply_excerpt=str(response or "")[:2000],
                        response_latency_ms=int((_time_mod.monotonic() - _chat_start_t) * 1000),
                        total_tokens=0,
                        cost_usd_micros=0,
                    )
                except Exception as _le:
                    logger.warning(f"[lineage] write failed for user {user_id}: {_le}")
            _spawn_background(_write_lineage())
        except Exception as _le_outer:
            logger.warning(f"[lineage] schedule failed for user {user_id}: {_le_outer}")

        return {"response": response}
    except Exception as e:
        logger.exception(f"Chat endpoint error for user {user_id}")
        try:
            import sentry_sdk as _sentry
            _sentry.capture_exception(e)
        except Exception:
            logger.warning("Sentry not available for error reporting")
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")


# ---------------------------------------------------------------------------
# /chat/transcribe — Voice-to-text for the Oracle composer
#
# Accepts a single audio file (webm/opus from Chrome, mp4/m4a from iOS Safari)
# and returns the transcript. Works on every browser that exposes
# MediaRecorder, including iOS Safari installed as a PWA — which is where the
# Web Speech API silently fails.
#
# Provider priority (first one with a key set wins):
#   1. GROQ_API_KEY      — whisper-large-v3-turbo, free tier, ~200ms / 30s clip
#   2. OPENAI_API_KEY    — whisper-1, ~$0.006/min
# Both share the same OpenAI-compatible /audio/transcriptions contract.
# ---------------------------------------------------------------------------
@app.post('/chat/transcribe', summary='Transcribe a short audio clip from the voice composer')
async def transcribe_audio(
    file: UploadFile = File(...),
    user_id: str = Depends(require_premium),
):
    import httpx

    groq_key = os.environ.get('GROQ_API_KEY', '').strip()
    openai_key = os.environ.get('OPENAI_API_KEY', '').strip()

    if groq_key:
        base_url = 'https://api.groq.com/openai/v1'
        model = 'whisper-large-v3-turbo'
        api_key = groq_key
        provider = 'groq'
    elif openai_key:
        base_url = 'https://api.openai.com/v1'
        model = 'whisper-1'
        api_key = openai_key
        provider = 'openai'
    else:
        raise HTTPException(
            status_code=503,
            detail="Voice transcription isn't configured on this server. Set GROQ_API_KEY or OPENAI_API_KEY.",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail='Empty audio payload.')

    # Soft cap at 25 MB — Whisper's own limit, and a sane ceiling for a single
    # composer clip. Longer audio should be split client-side.
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='Audio clip too long. Keep it under about 10 minutes.')

    filename = file.filename or 'audio.webm'
    content_type = file.content_type or 'audio/webm'

    files = {'file': (filename, contents, content_type)}
    data = {'model': model, 'response_format': 'json'}
    headers = {'Authorization': f'Bearer {api_key}'}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f'{base_url}/audio/transcriptions',
                headers=headers,
                files=files,
                data=data,
            )
    except httpx.RequestError as e:
        logger.warning(f"Transcription network error via {provider}: {e}")
        raise HTTPException(status_code=502, detail='Transcription service unreachable. Try again.')

    if r.status_code != 200:
        logger.warning(f"Transcription failed via {provider}: {r.status_code} {r.text[:300]}")
        raise HTTPException(
            status_code=502,
            detail=f"Transcription failed ({r.status_code}). Try again in a moment.",
        )

    try:
        payload = r.json()
    except ValueError:
        raise HTTPException(status_code=502, detail='Transcription returned an unexpected response.')

    transcript = (payload.get('text') or '').strip()
    return {'transcript': transcript, 'provider': provider}


class SynthesizeRequest(BaseModel):
    conversation_history: list[ChatMessage] = []


@app.post('/chat/synthesize', summary='Synthesize session memories on session close')
async def synthesize_session(
    req: SynthesizeRequest,
    user_id: str = Depends(require_premium),
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the frontend when the user leaves the chat (tab close, navigation away).
    Synthesizes the session into persistent memories so nothing is lost even if
    the in-session message count checkpoints never fired.

    Fires and forgets: returns immediately, synthesis runs as a background task.
    Requires at least 3 user messages to be worth synthesizing.
    """
    history = [{"role": m.role, "content": m.content} for m in req.conversation_history]
    user_message_count = sum(1 for m in history if m.get('role') == 'user')

    # Lower bar: even a 2-message exchange holds something worth carrying
    # forward, since short sessions are the norm.
    if user_message_count < 2:
        return {"ok": True, "synthesized": False, "reason": "too short"}

    blueprint = await get_blueprint(db, user_id)
    if not blueprint:
        return {"ok": True, "synthesized": False, "reason": "no blueprint"}

    from db.database import get_user_memories, update_user_memories
    memories = await get_user_memories(db, user_id)

    import asyncio
    from ai.chat import synthesize_memories

    async def _synthesize():
        try:
            new_memories = synthesize_memories(blueprint, history, memories)
            if new_memories:
                await update_user_memories(db, user_id, new_memories)
        except Exception as err:
            logger.warning(f"Session-close synthesis failed for user {user_id}: {err}")

    asyncio.create_task(_synthesize())
    return {"ok": True, "synthesized": True}


@app.get('/memory', summary='Get user memory entries')
async def get_memory(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Returns the current persistent memories the Higher Self holds about this user."""
    from db.database import get_user_memories
    memories = await get_user_memories(db, user_id)
    return {
        'memories': [
            {'category': m.category, 'content': m.content, 'updated_at': m.updated_at.isoformat()}
            for m in memories
        ],
        'count': len(memories)
    }


# ---------------------------------------------------------------------------
# Chat sessions — server-side storage so chat history syncs across devices
# ---------------------------------------------------------------------------

class ChatSessionUpsertRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    custom_name: Optional[str] = Field(None, max_length=255)
    date_label: Optional[str] = Field(None, max_length=64)
    messages: list[dict] = Field(default_factory=list)


@app.get('/chat/sessions', summary='List the current user\'s chat sessions (sorted by recency)')
async def chat_sessions_list(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    from db.database import list_chat_sessions
    sessions = await list_chat_sessions(db, user_id)
    return {'sessions': sessions}


@app.get('/chat/sessions/{session_id}', summary='Get a full chat session (messages included)')
async def chat_session_get(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    from db.database import get_chat_session
    import json as _json
    sess = await get_chat_session(db, user_id, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail='Session not found')
    try:
        messages = _json.loads(sess.messages_json or '[]')
    except Exception:
        messages = []
    return {
        'session_id': sess.id,
        'custom_name': sess.custom_name,
        'date_label': sess.date_label,
        'messages': messages,
        'last_message_at': sess.last_message_at.isoformat() if sess.last_message_at else None,
        'created_at': sess.created_at.isoformat() if sess.created_at else None,
    }


@app.put('/chat/sessions/{session_id}', summary='Upsert (create or update) a chat session')
async def chat_session_upsert(
    session_id: str,
    req: ChatSessionUpsertRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if req.session_id != session_id:
        raise HTTPException(status_code=400, detail='Session id mismatch')
    from db.database import upsert_chat_session
    sess = await upsert_chat_session(
        db, user_id,
        session_id=session_id,
        custom_name=req.custom_name,
        date_label=req.date_label,
        messages=req.messages,
    )
    return {
        'session_id': sess.id,
        'last_message_at': sess.last_message_at.isoformat() if sess.last_message_at else None,
    }


@app.delete('/chat/sessions/{session_id}', summary='Delete a chat session')
async def chat_session_delete(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    from db.database import delete_chat_session
    ok = await delete_chat_session(db, user_id, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail='Session not found')
    return {'deleted': True}


@app.delete('/memory', summary='Clear user memory')
async def clear_memory(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Clear all persistent memories for a fresh start.

    Hard-deletes every UserMemory row for this user. Returns the count
    actually removed so the client can confirm the clear took effect.
    Previously called update_user_memories(db, user_id, []) which is a
    merge-not-replace operation and never deleted anything; the user
    saw {'cleared': True} but kept all their old memories silently.
    """
    from db.database import delete_all_user_memories
    count = await delete_all_user_memories(db, user_id)
    logger.info(f"[memory] cleared {count} memories for user {user_id}")
    return {'cleared': True, 'deleted_count': count}



# ---------------------------------------------------------------------------
# GET /forecast/week — 7-day transit preview
# ---------------------------------------------------------------------------

@app.get('/forecast/week', summary="Get the 3 most significant transits for the next 7 days")
async def forecast_week(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Returns a week-ahead summary with the 3 most significant transits."""
    from datetime import date, timedelta
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    blueprint = await get_blueprint(db, user_id)
    if not blueprint:
        raise HTTPException(status_code=404, detail='Blueprint not found')

    today = date.today().isoformat()
    cached = await get_cached_forecast(db, user_id, f"week_{today}")
    if cached:
        return cached

    try:
        # Collect aspects from next 3 days (lightweight)
        significant = []
        for i in range(1, 4):
            day = (date.today() + timedelta(days=i)).isoformat()
            try:
                day_data = engines.get_daily_forecast(
                    birth_date=user.birth_date,
                    birth_time=user.birth_time,
                    birth_city=user.birth_city,
                    birth_lat=user.birth_lat,
                    birth_lon=user.birth_lon,
                )
                for asp in (day_data.get('aspects', []))[:1]:
                    significant.append({
                        'date': day,
                        'day_offset': i,
                        'transit_planet': asp.get('transit_planet'),
                        'aspect': asp.get('aspect'),
                        'natal_planet': asp.get('natal_planet'),
                        'orb': asp.get('orb', 99),
                    })
            except Exception as day_err:
                logger.warning(f"Failed to fetch forecast for day offset {i}: {day_err}")
                continue

        significant.sort(key=lambda x: x.get('orb', 99))
        top_3 = significant[:3]

        from ai.chat import _get_client, _build_system_prompt
        client = _get_client()
        transit_lines = "\n".join([
            f"- {t['date']}: {t['transit_planet']} {t['aspect']} natal {t['natal_planet']} (orb {t['orb']}°)"
            for t in top_3
        ]) if top_3 else "No major transits this week."

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=[
                {
                    "type": "text",
                    "text": _build_system_prompt(blueprint, None),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": f"In 1-2 sentences, what is the main theme of the coming days based on these transits:\n{transit_lines}\nSpeak directly to this person. Be specific."}]
        )

        result = {
            'week_summary': response.content[0].text.strip(),
            'top_transits': top_3,
            'generated_for': today,
        }
        await cache_forecast(db, user_id, f"week_{today}", result)
        return result
    except Exception as e:
        logger.exception(f"Weekly forecast failed for user {user_id}")
        raise HTTPException(status_code=500, detail=f'Weekly forecast failed: {str(e)}')


# ---------------------------------------------------------------------------
# POST /push/subscribe — Store Web Push subscription
# ---------------------------------------------------------------------------

@app.post('/push/subscribe', summary='Register push notification subscription')
async def push_subscribe(
    subscription_data: dict,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Store a Web Push subscription endpoint for sending transit alerts."""
    from sqlalchemy import text
    import uuid

    # Create table if not exists (non-blocking)
    try:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                endpoint TEXT NOT NULL,
                p256dh TEXT,
                auth TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        await db.commit()
    except Exception:
        pass

    sub = subscription_data.get('subscription', subscription_data)
    endpoint = sub.get('endpoint', '')
    keys = sub.get('keys', {})

    if not endpoint:
        return {'subscribed': False, 'error': 'No endpoint provided'}

    try:
        await db.execute(
            text("INSERT INTO push_subscriptions (id, user_id, endpoint, p256dh, auth) VALUES (:id, :uid, :ep, :p256, :auth) ON CONFLICT DO NOTHING"),
            {"id": str(uuid.uuid4()), "uid": user_id, "ep": endpoint, "p256": keys.get('p256dh', ''), "auth": keys.get('auth', '')}
        )
        await db.commit()
    except Exception:
        pass

    return {'subscribed': True}


# ---------------------------------------------------------------------------
# POST /push/native-subscribe — Store APNs / FCM device token
# ---------------------------------------------------------------------------
#
# Web push uses an "endpoint URL + p256dh + auth" tuple. Native iOS uses
# a 64-char APNs hex token. Native Android uses a (much longer) FCM
# string. They're not interchangeable, so we store them in a separate
# table keyed by (user_id, platform, device_token) with platform-side
# uniqueness so a single user with phone+tablet has multiple rows.
# ---------------------------------------------------------------------------

class NativePushSubscribeRequest(BaseModel):
    device_token: str = Field(..., min_length=8, max_length=512)
    platform:     str = Field(..., pattern=r'^(ios|android|unknown)$')
    app_version:  Optional[str] = Field(None, max_length=120)

@app.post('/push/native-subscribe', summary='Register a native APNs/FCM device token')
async def push_native_subscribe(
    req: NativePushSubscribeRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Store the APNs (iOS) or FCM (Android) device token for the
    authenticated user, so the backend can deliver native pushes via
    the appropriate provider later.

    Idempotent at the (user_id, device_token) level — re-registering
    the same token by the same user updates the platform/app_version
    in place rather than creating duplicate rows.
    """
    from sqlalchemy import text
    import uuid

    # Create the table if it doesn't exist. Schema chosen to be Postgres-
    # and SQLite-compatible. The unique constraint on (user_id, device_token)
    # is what makes the upsert safe.
    try:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS native_push_tokens (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                device_token TEXT NOT NULL,
                platform VARCHAR(16) NOT NULL,
                app_version VARCHAR(120),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (user_id, device_token)
            )
        """))
        await db.commit()
    except Exception as e:
        logger.warning("[push_native_subscribe] table create note: %s", e)

    try:
        # Upsert by (user_id, device_token) so a user re-launching the app
        # doesn't create infinite rows. Postgres UPSERT syntax via
        # ON CONFLICT works on Postgres; SQLite supports the same syntax
        # since 3.24+.
        await db.execute(
            text("""
                INSERT INTO native_push_tokens
                    (id, user_id, device_token, platform, app_version)
                VALUES
                    (:id, :uid, :tok, :plat, :ver)
                ON CONFLICT (user_id, device_token) DO UPDATE SET
                    platform = EXCLUDED.platform,
                    app_version = EXCLUDED.app_version,
                    updated_at = NOW()
            """),
            {
                "id":   str(uuid.uuid4()),
                "uid":  user_id,
                "tok":  req.device_token,
                "plat": req.platform,
                "ver":  req.app_version,
            },
        )
        await db.commit()
    except Exception as e:
        logger.warning("[push_native_subscribe] upsert failed: %s", e)
        return {"subscribed": False}

    return {"subscribed": True, "platform": req.platform}


# ===========================================================================
# ANALYTICS — event ingestion + privacy controls
# ===========================================================================
#
# What we track:    funnel events, feature usage, error events, subscription
#                   transitions. Event-shaped, aggregate-friendly.
# What we never track: chat/forecast content, identifiable behavior beyond
#                      user_id, third-party trackers.
# Retention:        90 days, auto-purged by analytics/retention.py cron.
# User control:     analytics_opt_out flag on User; respected on every
#                   insert. PATCH /users/analytics-opt-out toggles it.
# GDPR delete:      DELETE /users/me/analytics wipes a user's events on
#                   demand.
# ===========================================================================

class AnalyticsEventIn(BaseModel):
    event_name: str = Field(..., min_length=1, max_length=64,
                            pattern=r'^[a-z0-9_]+$',
                            description='Snake_case event name. No PII permitted.')
    session_id: str = Field(..., min_length=8, max_length=64,
                            description='Client-generated UUID grouping events from one session.')
    props:      Optional[dict] = Field(None, description='Tiny JSON payload, no user content.')


@app.post('/analytics/event', summary='Log an analytics event')
async def analytics_event(
    req: AnalyticsEventIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Best-effort analytics event ingestion. Never blocks the user flow:
    if the insert fails (DB hiccup, table not yet migrated), we return
    202 Accepted with a soft note rather than 5xx-ing the client.

    Privacy gates, in order:
      1. analytics_opt_out flag — if true, event is dropped silently.
      2. props size limit — anything bigger than 4 KB is truncated.
      3. event_name regex (Pydantic-enforced) — only snake_case ASCII,
         can't sneak in PII via clever event names.
    """
    from sqlalchemy import text
    import uuid as _uuid
    import json as _json

    # Honor opt-out without leaking that fact to the client (so the UI
    # can still call track() without branching).
    user = await get_user_by_id(db, user_id)
    if user and getattr(user, 'analytics_opt_out', False):
        return {"recorded": False, "reason": "opted_out"}

    props_blob = None
    if req.props is not None:
        try:
            props_blob = _json.dumps(req.props)[:4096]
        except Exception:
            props_blob = None

    try:
        await db.execute(
            text("""
                INSERT INTO analytics_events
                    (id, user_id, session_id, event_name, props)
                VALUES
                    (:id, :uid, :sid, :evt, :props)
            """),
            {
                "id":    str(_uuid.uuid4()),
                "uid":   user_id,
                "sid":   req.session_id,
                "evt":   req.event_name,
                "props": props_blob,
            },
        )
        await db.commit()
        return {"recorded": True}
    except Exception as e:
        logger.warning("[analytics] event insert failed (%s): %s", req.event_name, e)
        return {"recorded": False, "reason": "ingest_error"}


class AnalyticsOptOutRequest(BaseModel):
    opt_out: bool


@app.patch('/users/analytics-opt-out', summary='Toggle analytics tracking for the current user')
async def set_analytics_opt_out(
    req: AnalyticsOptOutRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """User-controlled toggle. true = stop recording; false = resume."""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    user.analytics_opt_out = bool(req.opt_out)
    await db.commit()
    return {"opt_out": bool(user.analytics_opt_out)}


@app.delete('/users/me/analytics', summary='Wipe the current user\'s analytics history (GDPR)')
async def delete_my_analytics(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Permanent deletion of every analytics_events row for this user.
    Runs synchronously; for active users this is at most a few hundred
    rows. Idempotent: zero rows is also a successful response.
    """
    from sqlalchemy import text
    try:
        result = await db.execute(
            text("DELETE FROM analytics_events WHERE user_id = :uid"),
            {"uid": user_id},
        )
        await db.commit()
        return {"deleted": result.rowcount or 0}
    except Exception as e:
        logger.warning("[analytics] user-data delete failed for %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail='Could not delete analytics history')


# ===========================================================================
# CANARY ALERTS — admin-only trigger; same logic the cron runs
# ===========================================================================

@app.post('/admin/canaries/run', summary='Manually run canary checks (admin only)')
async def admin_run_canaries(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Same logic the Railway cron job runs. Exposed as an HTTP endpoint
    so an operator can trigger a check from anywhere — no shell access
    required. Founder-gated.
    """
    user = await get_user_by_id(db, user_id)
    if not user or user.email not in {
        "kristjangilbert@gmail.com",
        "martakarenk@gmail.com",
        "davidsnaerj@gmail.com",
    }:
        raise HTTPException(status_code=403, detail='Founder-only')

    from analytics.canaries import run_canary_checks
    report = await run_canary_checks(db, send_alert=True)
    return report
# Force redeploy Tue Apr 14 18:15:01 CEST 2026


# ---------------------------------------------------------------------------
# Solray Business Hub — observability endpoints
#
# Read-only admin endpoints aggregating ApiUsage, OracleAudit, and friends
# into a single business-intelligence surface. Layer 1 (data) lives in the
# tables; Layer 2 (these endpoints) does the queries; Layer 3 (Jinja UI)
# renders them into a single hub page. All endpoints require admin auth.
#
# Shipped progressively. /admin/hub/cost was the first. Drift, lineage,
# users, and the Jinja UI layer follow in subsequent ships.
# ---------------------------------------------------------------------------

@app.get('/admin/hub/cost', summary='API usage and cost analytics (admin only)')
async def hub_cost(
    days: int = 7,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Returns token usage and USD cost rollups for the last N days.

    Pulls from api_usage. Costs are computed at write time using pricing.py
    so they survive future rate changes.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import select, func as sql_func
    from db.database import ApiUsage

    days = max(1, min(int(days or 7), 90))
    since = datetime.utcnow() - timedelta(days=days)

    # Totals
    totals_q = await db.execute(
        select(
            sql_func.count(ApiUsage.id).label('calls'),
            sql_func.coalesce(sql_func.sum(ApiUsage.total_tokens), 0).label('tokens'),
            sql_func.coalesce(sql_func.sum(ApiUsage.cost_usd_micros), 0).label('cost_micros'),
            sql_func.coalesce(sql_func.sum(ApiUsage.cache_read_tokens), 0).label('cache_read'),
            sql_func.coalesce(sql_func.sum(ApiUsage.input_tokens + ApiUsage.cache_creation_tokens), 0).label('input_plus_create'),
        ).where(ApiUsage.created_at >= since)
    )
    t = totals_q.first()
    total_calls = int(t.calls or 0)
    total_tokens = int(t.tokens or 0)
    total_cost_usd = (t.cost_micros or 0) / 1_000_000.0
    cache_hit_ratio = 0.0
    if (t.input_plus_create or 0) > 0:
        cache_hit_ratio = (t.cache_read or 0) / float((t.cache_read or 0) + (t.input_plus_create or 0))

    # By surface
    by_surface_q = await db.execute(
        select(
            ApiUsage.surface,
            sql_func.count(ApiUsage.id).label('calls'),
            sql_func.coalesce(sql_func.sum(ApiUsage.total_tokens), 0).label('tokens'),
            sql_func.coalesce(sql_func.sum(ApiUsage.cost_usd_micros), 0).label('cost_micros'),
        ).where(ApiUsage.created_at >= since).group_by(ApiUsage.surface)
    )
    by_surface = [
        {
            "surface": r.surface,
            "calls": int(r.calls or 0),
            "tokens": int(r.tokens or 0),
            "cost_usd": (r.cost_micros or 0) / 1_000_000.0,
        }
        for r in by_surface_q.fetchall()
    ]
    by_surface.sort(key=lambda x: x["cost_usd"], reverse=True)

    # By model
    by_model_q = await db.execute(
        select(
            ApiUsage.model,
            sql_func.count(ApiUsage.id).label('calls'),
            sql_func.coalesce(sql_func.sum(ApiUsage.total_tokens), 0).label('tokens'),
            sql_func.coalesce(sql_func.sum(ApiUsage.cost_usd_micros), 0).label('cost_micros'),
        ).where(ApiUsage.created_at >= since).group_by(ApiUsage.model)
    )
    by_model = [
        {
            "model": r.model,
            "calls": int(r.calls or 0),
            "tokens": int(r.tokens or 0),
            "cost_usd": (r.cost_micros or 0) / 1_000_000.0,
        }
        for r in by_model_q.fetchall()
    ]
    by_model.sort(key=lambda x: x["cost_usd"], reverse=True)

    # Top users by cost
    top_users_q = await db.execute(
        select(
            ApiUsage.user_id,
            sql_func.count(ApiUsage.id).label('calls'),
            sql_func.coalesce(sql_func.sum(ApiUsage.cost_usd_micros), 0).label('cost_micros'),
        ).where(ApiUsage.created_at >= since, ApiUsage.user_id.is_not(None)).group_by(ApiUsage.user_id)
    )
    top_users = [
        {
            "user_id": r.user_id,
            "calls": int(r.calls or 0),
            "cost_usd": (r.cost_micros or 0) / 1_000_000.0,
        }
        for r in top_users_q.fetchall()
    ]
    top_users.sort(key=lambda x: x["cost_usd"], reverse=True)
    top_users = top_users[:10]

    # Errors
    err_q = await db.execute(
        select(sql_func.count(ApiUsage.id))
        .where(ApiUsage.created_at >= since, ApiUsage.is_success.is_(False))
    )
    error_count = int(err_q.scalar() or 0)
    error_rate = (error_count / total_calls) if total_calls else 0.0

    # Queue health (writer stats)
    try:
        from ai.usage_logger import get_queue_stats
        queue_stats = get_queue_stats()
    except Exception:
        queue_stats = {"queue_depth": -1, "enabled": False}

    return {
        "window_days": days,
        "since": since.isoformat() + "Z",
        "totals": {
            "calls": total_calls,
            "tokens": total_tokens,
            "cost_usd": round(total_cost_usd, 4),
            "errors": error_count,
            "error_rate": round(error_rate, 4),
            "cache_hit_ratio": round(cache_hit_ratio, 4),
        },
        "by_surface": by_surface,
        "by_model": by_model,
        "top_users_by_cost": top_users,
        "queue_stats": queue_stats,
    }


@app.get('/admin/hub/lineage/{request_uuid}', summary='Full provenance trail for one chat request (admin only)')
async def hub_lineage_one(
    request_uuid: str,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Returns a single ChatLineage row plus joined ApiUsage entries and the
    OracleAudit row for that request. Use it when a user complains about a
    specific reply: paste the request_uuid and get the whole picture.
    """
    from sqlalchemy import select
    from db.database import ChatLineage, ApiUsage, OracleAudit
    lin_q = await db.execute(select(ChatLineage).where(ChatLineage.request_uuid == request_uuid))
    lin = lin_q.scalar_one_or_none()
    if lin is None:
        raise HTTPException(status_code=404, detail='request_uuid not found')

    usage_q = await db.execute(select(ApiUsage).where(ApiUsage.request_uuid == request_uuid))
    usage = usage_q.scalars().all()

    audit = None
    if lin.audit_id:
        audit_q = await db.execute(select(OracleAudit).where(OracleAudit.id == lin.audit_id))
        audit = audit_q.scalar_one_or_none()

    return {
        "lineage": {
            "request_uuid": lin.request_uuid,
            "user_id": lin.user_id,
            "session_id": lin.session_id,
            "created_at": lin.created_at.isoformat() + 'Z' if lin.created_at else None,
            "prompt_hash": lin.prompt_hash,
            "system_prompt_version": lin.system_prompt_version,
            "retrieval_version": lin.retrieval_version,
            "model": lin.model,
            "is_break_glass": lin.is_break_glass,
            "retrieved_memory_count": lin.retrieved_memory_count,
            "retrieved_event_count": lin.retrieved_event_count,
            "hive_context_used": lin.hive_context_used,
            "user_message_excerpt": lin.user_message_excerpt,
            "reply_excerpt": lin.reply_excerpt,
            "response_latency_ms": lin.response_latency_ms,
            "audit_id": lin.audit_id,
            "audit_score": lin.audit_score,
            "total_tokens": lin.total_tokens,
            "cost_usd": (lin.cost_usd_micros or 0) / 1_000_000.0,
        },
        "usage_calls": [
            {
                "id": u.id,
                "surface": u.surface,
                "provider": u.provider,
                "model": u.model,
                "tokens": u.total_tokens,
                "cost_usd": (u.cost_usd_micros or 0) / 1_000_000.0,
                "duration_ms": u.duration_ms,
                "is_success": u.is_success,
                "retries": u.retries,
                "error_type": u.error_type,
            } for u in usage
        ],
        "audit": ({
            "id": audit.id,
            "score": audit.score,
            "violations_json": audit.violations_json,
            "notes": audit.notes,
            "model_used": audit.model_used,
            "oracle_prompt_version": audit.oracle_prompt_version,
            "audit_prompt_version": audit.audit_prompt_version,
            "created_at": audit.created_at.isoformat() + 'Z' if audit.created_at else None,
        } if audit else None),
    }


@app.get('/admin/hub/overview', summary='Solray Business Hub top-level KPIs (admin only)')
async def hub_overview(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """One-call snapshot of how Solray is running right now: users, MRR,
    audit voice quality, AI spend, error rates, and queue health. Built
    to be the main /admin/hub landing-page payload.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import select, func as sql_func
    from db.database import (
        User, OracleAudit, ApiUsage, ChatLineage,
        UserMemory, NarrativeEvent,
    )
    from payments.models import Subscription

    now = datetime.utcnow()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)
    last_30d = now - timedelta(days=30)

    # Users + subscribers (subscription state lives on Subscription table,
    # not User; status values: trial | active | past_due | cancelled | expired)
    total_users_q = await db.execute(select(sql_func.count(User.id)))
    total_users = int(total_users_q.scalar() or 0)

    active_subs_q = await db.execute(
        select(sql_func.count(Subscription.id)).where(Subscription.status.in_(['trial', 'active', 'past_due']))
    )
    active_subs = int(active_subs_q.scalar() or 0)

    paying_subs_q = await db.execute(
        select(sql_func.count(Subscription.id)).where(Subscription.status == 'active')
    )
    paying_subs = int(paying_subs_q.scalar() or 0)

    # Audit voice quality (last 7 days)
    audit_q = await db.execute(
        select(
            sql_func.avg(OracleAudit.score).label('avg'),
            sql_func.count(OracleAudit.id).label('n'),
        ).where(OracleAudit.created_at >= last_7d)
    )
    a = audit_q.first()
    audit_7d = {"avg_score": round(float(a.avg or 0), 2), "samples": int(a.n or 0)}

    # AI cost (7 days)
    cost_q = await db.execute(
        select(
            sql_func.coalesce(sql_func.sum(ApiUsage.cost_usd_micros), 0).label('cost'),
            sql_func.count(ApiUsage.id).label('calls'),
        ).where(ApiUsage.created_at >= last_7d)
    )
    c = cost_q.first()
    spend_7d = {"cost_usd": (c.cost or 0) / 1_000_000.0, "calls": int(c.calls or 0)}

    # Error rate (24h)
    err_q = await db.execute(
        select(
            sql_func.count(ApiUsage.id).label('all'),
            sql_func.sum(sql_func.case((ApiUsage.is_success.is_(False), 1), else_=0)).label('err'),
        ).where(ApiUsage.created_at >= last_24h)
    )
    e = err_q.first()
    err_24h = {
        "total": int(e.all or 0),
        "errors": int(e.err or 0),
        "error_rate": round(float((e.err or 0) / e.all), 4) if (e.all or 0) > 0 else 0.0,
    }

    # Chat volume (24h)
    chat_q = await db.execute(
        select(sql_func.count(ChatLineage.id)).where(ChatLineage.created_at >= last_24h)
    )
    chats_24h = int(chat_q.scalar() or 0)

    # Memory + narrative size
    mem_q = await db.execute(select(sql_func.count(UserMemory.id)))
    nev_q = await db.execute(select(sql_func.count(NarrativeEvent.id)))
    mem_count = int(mem_q.scalar() or 0)
    nev_count = int(nev_q.scalar() or 0)

    # Queue health
    try:
        from ai.usage_logger import get_queue_stats
        queue = get_queue_stats()
    except Exception:
        queue = {"queue_depth": -1, "enabled": False}

    # MRR estimate (paying users * 23 USD)
    mrr_usd = paying_subs * 23.0

    return {
        "now": now.isoformat() + 'Z',
        "users": {
            "total": total_users,
            "active_or_trialing": active_subs,
            "paying": paying_subs,
        },
        "revenue": {
            "mrr_usd_estimate": mrr_usd,
        },
        "voice_quality_7d": audit_7d,
        "ai_spend_7d": spend_7d,
        "errors_24h": err_24h,
        "chats_24h": chats_24h,
        "memory": {
            "user_memory_rows": mem_count,
            "narrative_event_rows": nev_count,
        },
        "usage_queue": queue,
    }


@app.post('/admin/hub/cron/drift', summary='Run audit drift detection (admin only, cron-triggered)')
async def hub_cron_drift(
    surface: str = "chat",
    window_days: int = 7,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Runs Page-Hinkley over the last N days of OracleAudit scores. Writes
    an AuditDriftAlert row if drift is detected. Returns the result.

    Designed to be Railway-cron-triggered: a daily POST to this endpoint.
    Heartbeat is recorded in cron_heartbeats so the hub can show last-run
    status per job.
    """
    from datetime import datetime
    from db.database import CronHeartbeat
    import time as _t
    started = datetime.utcnow()
    start_t = _t.monotonic()
    job_name = f"drift_detector__{surface}"
    notes = None
    success = False
    try:
        from ai.drift_detector import detect_audit_drift
        result = await detect_audit_drift(db, surface=surface, window_days=int(window_days))
        success = True
        notes = f"alert_fired={result.get('alert_fired')} samples={result.get('samples')} stat={result.get('statistic', 0):.2f}"
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
        notes = str(e)[:500]
    # Record heartbeat (separate session so it survives if detect crashed)
    try:
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as sess:
            hb = CronHeartbeat(
                job_name=job_name,
                started_at=started,
                finished_at=datetime.utcnow(),
                success=success,
                duration_ms=int((_t.monotonic() - start_t) * 1000),
                notes=notes,
            )
            sess.add(hb)
            await sess.commit()
    except Exception:
        pass
    return result


@app.get('/admin/hub/drift', summary='Recent audit drift alerts (admin only)')
async def hub_drift(
    limit: int = 20,
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Returns recent AuditDriftAlert rows, newest first."""
    from sqlalchemy import select
    from db.database import AuditDriftAlert
    q = await db.execute(
        select(AuditDriftAlert).order_by(AuditDriftAlert.created_at.desc()).limit(max(1, min(int(limit), 100)))
    )
    rows = q.scalars().all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() + "Z",
            "surface": r.surface,
            "metric": r.metric,
            "window_days": r.window_days,
            "value": r.value,
            "threshold": r.threshold,
            "samples": r.samples,
            "status": r.status,
            "notes": r.notes,
            "resolved_at": r.resolved_at.isoformat() + "Z" if r.resolved_at else None,
        } for r in rows
    ]


@app.get('/admin/hub/cron', summary='Cron heartbeat status (admin only)')
async def hub_cron_status(
    admin_id: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Returns the most recent run per cron job. Lets the hub show at a
    glance whether scheduled jobs are firing.
    """
    from sqlalchemy import select, func as sql_func
    from db.database import CronHeartbeat
    # Last 50 rows is enough to find latest per job for the few jobs we run
    q = await db.execute(
        select(CronHeartbeat).order_by(CronHeartbeat.started_at.desc()).limit(200)
    )
    rows = q.scalars().all()
    latest = {}
    for r in rows:
        if r.job_name in latest:
            continue
        latest[r.job_name] = {
            "job_name": r.job_name,
            "last_started_at": r.started_at.isoformat() + "Z",
            "last_finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None,
            "last_success": bool(r.success),
            "duration_ms": r.duration_ms,
            "notes": r.notes,
        }
    return {"jobs": list(latest.values())}


@app.get('/admin/hub', summary='Solray Business Hub UI (HTML)')
async def hub_ui():
    """The hub page itself. Open to anyone (no server-side auth on this
    route), but the JSON endpoints it calls require admin auth via JWT.
    The HTML contains zero user data and zero secrets - it is just a
    JavaScript client that asks the user for their JWT and then calls
    the protected /admin/hub/* endpoints on their behalf.
    """
    from fastapi.responses import HTMLResponse
    from api.hub_html import HUB_HTML
    return HTMLResponse(content=HUB_HTML, status_code=200)
