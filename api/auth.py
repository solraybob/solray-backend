"""
api/auth.py — Authentication & Authorization for Solray AI

Handles:
  - Password hashing with bcrypt
  - JWT token creation and verification
  - FastAPI dependency for protected routes
"""

import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# In production: load from environment variables / secrets manager
SECRET_KEY = os.environ.get('JWT_SECRET', os.environ.get('JWT_SECRET_KEY', 'solray-dev-secret-change-in-production-please'))
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_HOURS = int(os.environ.get('TOKEN_EXPIRE_HOURS', '720'))  # 30 days default

# ---------------------------------------------------------------------------
# Password Hashing (bcrypt direct — avoids passlib version issues)
# ---------------------------------------------------------------------------

def hash_password(plain_password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    # bcrypt requires bytes; truncate to 72 bytes (bcrypt limit)
    pw_bytes = plain_password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pw_bytes, salt).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    pw_bytes = plain_password.encode('utf-8')[:72]
    hash_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(pw_bytes, hash_bytes)


# ---------------------------------------------------------------------------
# JWT Tokens
# ---------------------------------------------------------------------------

def create_access_token(user_id: str, email: str) -> str:
    """
    Create a signed JWT access token.
    
    Payload:
      sub: user_id (primary identifier)
      email: for convenience
      exp: expiry timestamp
      iat: issued-at timestamp
    """
    now = datetime.utcnow()
    expire = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    
    payload = {
        'sub': user_id,
        'email': email,
        'iat': now,
        'exp': expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and validate a JWT token.
    Raises HTTPException 401 if invalid or expired.
    Returns the decoded payload dict.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Could not validate credentials',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get('sub')
        if user_id is None:
            raise credentials_exception
        return payload
    except JWTError:
        raise credentials_exception


# ---------------------------------------------------------------------------
# FastAPI Auth Dependency
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer()


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> str:
    """
    FastAPI dependency. Validates Bearer token and returns user_id.
    Use in any protected route: user_id: str = Depends(get_current_user_id)
    """
    token = credentials.credentials
    payload = decode_access_token(token)
    return payload['sub']
