"""Staff login: password hashing, JWT tokens, and role checks for the dashboard API.

Two roles:
- agent: reads conversations and traces, approves / edits / rejects AI replies, replies as a human,
         works the escalations queue, sees analytics.
- admin: everything an agent can do, plus refund approvals (money), settings and the feedback export.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .db import SessionLocal
from .models import User

log = logging.getLogger(__name__)

_PBKDF2_ROUNDS = 310_000
_secret = get_settings().jwt_secret or secrets.token_urlsafe(48)
if not get_settings().jwt_secret:
    log.warning("JWT_SECRET is not set: using a random secret, so staff are signed out whenever the server restarts")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS).hex()
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(check, digest)


def make_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=get_settings().jwt_hours)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, _secret, algorithm="HS256")


def _user_from_token(token: str) -> User:
    try:
        payload = jwt.decode(token, _secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")
    with SessionLocal() as s:
        user = s.get(User, int(payload["sub"]))
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or disabled")
    return user


_bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return _user_from_token(creds.credentials)


def current_user_query(token: str = Query(...)) -> User:
    """For Server-Sent Events: the browser's EventSource cannot send headers, so the token comes in the URL."""
    return _user_from_token(token)


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user


def seed_staff() -> None:
    """Make sure the admin and agent accounts from the settings exist with the configured passwords.

    Runs at every start, so changing ADMIN_PASSWORD / AGENT_PASSWORD in the environment and restarting
    is how passwords are rotated (also on a deployed copy that shipped with an older database)."""
    s_ = get_settings()
    wanted = [(s_.admin_email, s_.admin_password, "admin", "Admin"),
              (s_.agent_email, s_.agent_password, "agent", "Support Agent")]
    with SessionLocal() as s:
        for email, password, role, name in wanted:
            email = email.strip().lower()
            u = s.query(User).filter(User.email == email).first()
            if u is None:
                s.add(User(email=email, name=name, role=role, password_hash=hash_password(password)))
                log.warning("Created staff account %s (%s)", email, role)
            elif not verify_password(password, u.password_hash) or u.role != role:
                u.password_hash, u.role, u.active = hash_password(password), role, True
                log.warning("Updated password/role for %s from settings", email)
        s.commit()
    if s_.admin_password == "admin-demo-123" or s_.agent_password == "agent-demo-123":
        log.warning("Staff accounts use the public demo passwords. Set ADMIN_PASSWORD / AGENT_PASSWORD before going live.")
