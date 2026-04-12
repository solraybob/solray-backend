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
from datetime import date, datetime
from typing import Optional, List

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

from fastapi import FastAPI, Depends, HTTPException, status
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
    User
)
import engines
from ai.forecast import generate_daily_forecast
from ai.chat import chat as higher_self_chat, group_chat as group_higher_self_chat
from energy_calculator import calculate_energy_scores
from lunar import get_upcoming_lunar_event

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

# CORS — allow all origins in dev; tighten for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.on_event('startup')
async def startup():
    """Initialise DB tables on first start."""
    await init_db()


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    name:       str        = Field(..., example='Alice Sun')
    email:      EmailStr   = Field(..., example='alice@example.com')
    password:   str        = Field(..., min_length=6, example='s3cr3t')
    birth_date: str        = Field(..., example='1990-06-15', description='YYYY-MM-DD')
    birth_time: str        = Field(..., example='14:30',      description='HH:MM')
    birth_city: str        = Field(..., example='London')
    sex:        Optional[str] = Field(None, example='female', description="'male' or 'female'")
    tz_offset:  float      = Field(0.0, example=1.0, description='UTC offset at birth (e.g. 1.0 for BST)')
    username:   Optional[str] = Field(None, example='alicesun', description='Optional username (auto-generated if omitted)')


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
        'id':         user.id,
        'email':      user.email,
        'username':   user.username,
        'name':       user.name,
        'birth_date': user.birth_date,
        'birth_time': user.birth_time,
        'birth_city': user.birth_city,
        'birth_lat':  user.birth_lat,
        'birth_lon':  user.birth_lon,
        'sex':           getattr(user, 'sex', None),
        'profile_photo': getattr(user, 'profile_photo', None),
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

    # Create user row
    user_id = str(uuid.uuid4())
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
        # Blueprint calculation failed — user is created but without blueprint
        # Capture to Sentry for debugging
        try:
            import sentry_sdk as _sentry
            _sentry.capture_exception(e)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=f'User created but blueprint calculation failed: {str(e)}'
        )

    # Store blueprint
    await upsert_blueprint(db, user_id, blueprint)

    # Issue JWT
    token = create_access_token(user_id=user_id, email=req.email)

    return {
        'user_id':   user_id,
        'token':     token,
        'profile':   _user_profile(user),
        'blueprint': blueprint,
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
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
        }
    }


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
    }


# ---------------------------------------------------------------------------
# PATCH /users/profile
# ---------------------------------------------------------------------------

class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None

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
        # Check uniqueness
        existing = await get_user_by_username(db, req.username)
        if existing and existing.id != user_id:
            raise HTTPException(status_code=400, detail='Username already taken')
        user.username = req.username

    await db.commit()
    await db.refresh(user)
    return {'name': user.name, 'username': user.username}

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

    if not req.photo.startswith('data:image/'):
        raise HTTPException(status_code=400, detail='Photo must be a base64 image data URI')

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
# GET /forecast/today
# ---------------------------------------------------------------------------

@app.get('/forecast/today', summary="Get today's personalised AI-generated forecast")
async def forecast_today(
    refresh: bool = False,
    user_id: str = Depends(get_current_user_id),
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
            try:
                import sentry_sdk as _sentry
                _sentry.capture_exception(e)
            except Exception:
                pass
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

        # Get summary chart fields (not full blueprint, privacy)
        bp = await get_blueprint(db, other_id)
        sun_sign = None
        moon_sign = None
        hd_type = None
        hd_profile = None
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
                'id':         other_user.id,
                'username':   other_user.username,
                'name':       other_user.name,
                'sun_sign':   sun_sign,
                'moon_sign':  moon_sign,
                'hd_type':    hd_type,
                'hd_profile': hd_profile,
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
    user_id: str = Depends(get_current_user_id),
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
# GET /souls/{connection_id}/blueprint
# ---------------------------------------------------------------------------

@app.get('/souls/{connection_id}/blueprint', summary="Get a soul connection's full blueprint (for chat only)")
async def soul_blueprint_for_chat(
    connection_id: str,
    user_id: str = Depends(get_current_user_id),
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
    user_id: str = Depends(get_current_user_id),
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
        return {'response': response}
    except Exception as e:
        try:
            import sentry_sdk as _sentry
            _sentry.capture_exception(e)
        except Exception:
            pass
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
async def recalculate_blueprint(email: str, db: AsyncSession = Depends(get_db)):
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
async def recalculate_all_blueprints(db: AsyncSession = Depends(get_db)):
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
    user_id: str = Depends(get_current_user_id),
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
        raise HTTPException(status_code=500, detail=f'Astrocartography calculation failed: {str(e)}')



# ---------------------------------------------------------------------------
# GET /transits/long-range
# ---------------------------------------------------------------------------

@app.get('/transits/long-range', summary="Get the user's active long-range astrological cycles")
async def long_range_transits(
    user_id: str = Depends(get_current_user_id),
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
        try:
            import sentry_sdk as _sentry
            _sentry.capture_exception(e)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f'Long-range transit calculation failed: {str(e)}')

    # Generate AI summaries if we have transits
    if transits:
        try:
            from ai.long_range_ai import generate_transit_summaries
            transits = generate_transit_summaries(transits, blueprint)
        except Exception as e:
            # AI summaries failed — return transits without summaries
            try:
                import sentry_sdk as _sentry
                _sentry.capture_exception(e)
            except Exception:
                pass

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
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    blueprint = await get_blueprint(db, user_id)
    if not blueprint:
        raise HTTPException(status_code=404, detail='Blueprint not found')
    from datetime import date
    forecast = await get_cached_forecast(db, user_id, date.today().isoformat())
    history = [{"role": m.role, "content": m.content} for m in req.conversation_history]

    # Load persistent user memories for continuity across sessions
    from db.database import get_user_memories, update_user_memories
    memories = await get_user_memories(db, user_id)

    try:
        response = higher_self_chat(
            blueprint=blueprint,
            forecast=forecast,
            conversation_history=history,
            user_message=req.message,
            soul_blueprint=req.soul_blueprint,
            memories=memories,
        )

        # After every 5 user messages, synthesize memories in background
        user_message_count = sum(1 for m in history if m.get('role') == 'user')
        if user_message_count > 0 and user_message_count % 5 == 0:
            import asyncio
            from ai.chat import synthesize_memories
            async def _synthesize():
                try:
                    new_memories = synthesize_memories(blueprint, history, memories)
                    if new_memories:
                        await update_user_memories(db, user_id, new_memories)
                except Exception:
                    pass
            asyncio.create_task(_synthesize())

        return {"response": response}
    except Exception as e:
        try:
            import sentry_sdk as _sentry
            _sentry.capture_exception(e)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")


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


@app.delete('/memory', summary='Clear user memory')
async def clear_memory(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Clear all persistent memories for fresh start."""
    from db.database import update_user_memories
    await update_user_memories(db, user_id, [])
    return {'cleared': True}



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
            except Exception:
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
            system=_build_system_prompt(blueprint, None),
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
