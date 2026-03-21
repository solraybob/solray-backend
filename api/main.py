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

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

# Add project root to path so engines.py is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api.auth import (
    hash_password, verify_password,
    create_access_token, get_current_user_id
)
from db.database import (
    get_db, init_db, AsyncSession,
    create_user, get_user_by_id, get_user_by_email,
    upsert_blueprint, get_blueprint,
    get_cached_forecast, cache_forecast,
    create_soul_invite, get_soul_connection,
    accept_soul_connection, get_accepted_souls,
    User
)
import engines
from ai.forecast import generate_daily_forecast
from ai.chat import chat as higher_self_chat

# ---------------------------------------------------------------------------
# App Setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title='Solray AI',
    description='Personal astrology + Human Design + Gene Keys blueprint API',
    version='0.2.0',
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
    password:   str        = Field(..., min_length=8, example='s3cr3tpass')
    birth_date: str        = Field(..., example='1990-06-15', description='YYYY-MM-DD')
    birth_time: str        = Field(..., example='14:30',      description='HH:MM')
    birth_city: str        = Field(..., example='London')
    tz_offset:  float      = Field(0.0, example=1.0, description='UTC offset at birth (e.g. 1.0 for BST)')


class LoginRequest(BaseModel):
    email:    EmailStr = Field(..., example='alice@example.com')
    password: str      = Field(..., example='s3cr3tpass')


class TokenResponse(BaseModel):
    token:   str
    user_id: str


class InviteRequest(BaseModel):
    email: EmailStr = Field(..., description='Email of the person to invite')


class ChatMessage(BaseModel):
    role: str = Field(..., description='Either "user" or "assistant"')
    content: str = Field(..., description='Message content')


class ChatRequest(BaseModel):
    message: str = Field(..., description='The user message to the Higher Self')
    conversation_history: List[ChatMessage] = Field(
        default=[], description='Prior conversation turns'
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
# Helper: build user profile dict
# ---------------------------------------------------------------------------

def _user_profile(user: User) -> dict:
    return {
        'id':         user.id,
        'email':      user.email,
        'name':       user.name,
        'birth_date': user.birth_date,
        'birth_time': user.birth_time,
        'birth_city': user.birth_city,
        'birth_lat':  user.birth_lat,
        'birth_lon':  user.birth_lon,
        'created_at': user.created_at.isoformat() if user.created_at else None,
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

    # Geocode the birth city (raises ValueError if not found)
    try:
        from astrology import geocode_city
        birth_lat, birth_lon = geocode_city(req.birth_city)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Create user row
    user_id = str(uuid.uuid4())
    user = await create_user(db, {
        'id':            user_id,
        'email':         req.email,
        'name':          req.name,
        'password_hash': hash_password(req.password),
        'birth_date':    req.birth_date,
        'birth_time':    req.birth_time,
        'birth_city':    req.birth_city,
        'birth_lat':     birth_lat,
        'birth_lon':     birth_lon,
    })

    # Build the full blueprint (this is the expensive calculation)
    try:
        blueprint = engines.build_blueprint(
            birth_date=req.birth_date,
            birth_time=req.birth_time,
            birth_city=req.birth_city,
            birth_lat=birth_lat,
            birth_lon=birth_lon,
            tz_offset=req.tz_offset,
        )
    except Exception as e:
        # Blueprint calculation failed — user is created but without blueprint
        # Return partial success; client can retry blueprint generation
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

    return {
        'profile':   _user_profile(user),
        'blueprint': blueprint,
    }


# ---------------------------------------------------------------------------
# GET /forecast/today
# ---------------------------------------------------------------------------

@app.get('/forecast/today', summary="Get today's personalised AI-generated forecast")
async def forecast_today(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns today's personalised AI-generated forecast for the authenticated user.

    The forecast is generated once per day and cached. It includes:
      - AI-generated title and reading (Higher Self voice)
      - tags (astrology, human_design, gene_keys)
      - energy_levels (mental, emotional, physical, intuitive — 0-100)
      - dominant_transit — the most significant planetary aspect today
      - hd_gate_today — today's active HD gate with Gene Key shadow/gift
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')

    today_str = date.today().isoformat()

    # Check cache first
    cached = await get_cached_forecast(db, user_id, today_str)
    if cached:
        # If cached forecast already has AI fields, return it directly
        if 'title' in cached and 'reading' in cached:
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
            ai_forecast = {'_ai_error': str(e)}

    # Merge: AI fields + raw data for richer access
    final_forecast = {
        **forecast_data,
        **ai_forecast,
        '_cached': False,
        '_generated_at': datetime.utcnow().isoformat(),
    }

    # Also add the legacy summary for backward compat
    if 'summary' not in final_forecast:
        final_forecast['summary'] = _build_forecast_summary(forecast_data)

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
# POST /souls/invite
# ---------------------------------------------------------------------------

@app.post('/souls/invite', summary='Invite someone as a soul connection', status_code=201)
async def invite_soul(
    req: InviteRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a soul connection invite to another user by email.
    The invited user must already have a Solray account.
    Creates a pending soul_connection record.
    """
    # Can't invite yourself
    requester = await get_user_by_id(db, user_id)
    if requester and requester.email == req.email:
        raise HTTPException(status_code=400, detail='Cannot invite yourself')

    recipient = await get_user_by_email(db, req.email)
    if not recipient:
        raise HTTPException(
            status_code=404,
            detail='No Solray account found with that email. Ask them to sign up first!'
        )

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
        'invite_id':     invite.id,
        'recipient_name': recipient.name,
        'recipient_email': recipient.email,
        'status':        invite.status,
        'created_at':    invite.created_at.isoformat(),
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
        'message':   'Soul connection accepted! ✨',
    }


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

    for conn in connections:
        # Determine which side is the "other" person
        other_id = conn.recipient_id if conn.requester_id == user_id else conn.requester_id
        other_user = await get_user_by_id(db, other_id)
        if not other_user:
            continue

        # Get their blueprint summary (not full JSON — keep response lean)
        bp = await get_blueprint(db, other_id)
        summary = bp.get('summary') if bp else None

        result.append({
            'connection_id': conn.id,
            'soul': {
                'id':         other_user.id,
                'name':       other_user.name,
                'email':      other_user.email,
                'birth_date': other_user.birth_date,
                'birth_city': other_user.birth_city,
                'summary':    summary,
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
        'version': '0.2.0',
        'docs': '/docs',
    }


# Admin: delete user (for testing only — remove before public launch)
@app.delete('/admin/users/{email}', summary="Delete user by email (admin only)")
async def delete_user(email: str, db: AsyncSession = Depends(get_db)):
    """Delete a user and all their data."""
    from sqlalchemy import text
    await db.execute(text("DELETE FROM daily_forecasts WHERE user_id IN (SELECT id FROM users WHERE email = :email)"), {"email": email})
    await db.execute(text("DELETE FROM blueprints WHERE user_id IN (SELECT id FROM users WHERE email = :email)"), {"email": email})
    await db.execute(text("DELETE FROM soul_connections WHERE requester_id IN (SELECT id FROM users WHERE email = :email) OR recipient_id IN (SELECT id FROM users WHERE email = :email)"), {"email": email})
    await db.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
    await db.commit()
    return {"deleted": email}
