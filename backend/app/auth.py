"""Interactive authentication: password sign-in, signed session tokens, logout (ZA-IAM-001 baseline).

* Passwords are hashed with scrypt (stdlib) and a per-credential salt; comparison is constant-time.
* A session token is ``base64url(payload).base64url(HMAC-SHA256)`` keyed by a local secret, and
  it references a server-side ``AuthSession`` row so logout revokes it immediately.
* Only HUMAN and EXTERNAL_AUDITOR actors may sign in interactively; agents and services never
  hold passwords (INV-01 — they cannot become the accountable authoriser by logging in).
* Repeated failures lock the credential temporarily. Error messages never reveal whether an
  account exists.

Production replaces this module with OIDC/SAML federation + SCIM; the ``Ctx`` contract is unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import DATA_DIR, get_db, set_tenant_scope
from .models import Actor, AuthCredential, AuthSession, Tenant
from .platform import audit, ensure_utc, to_dict

INTERACTIVE_TYPES = {"HUMAN", "EXTERNAL_AUDITOR"}
SESSION_HOURS = int(os.environ.get("ZA_SESSION_HOURS", "8"))
MAX_FAILURES = 5
LOCK_MINUTES = 15
MIN_PASSWORD = 10
# Demo/dev only and off by default. There is deliberately no built-in demo password: demo sign-in exists only when
# ZA_DEMO_MODE=1 *and* a locally chosen ZA_DEMO_PASSWORD are both set.
DEMO_MODE = os.environ.get("ZA_DEMO_MODE", "0") == "1"
DEMO_PASSWORD = os.environ.get("ZA_DEMO_PASSWORD", "")

_KEY_PATH = DATA_DIR / "keys" / "session.key"


def _session_key() -> bytes:
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _KEY_PATH.exists():
        _KEY_PATH.write_bytes(secrets.token_bytes(32))
    return _KEY_PATH.read_bytes()


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_hex(8))  # equalises timing for unknown accounts


def password_policy_ok(pw: str) -> bool:
    return len(pw) >= MIN_PASSWORD and pw.lower() != pw and pw.upper() != pw and any(c.isdigit() for c in pw)


def generate_password() -> str:
    """A strong random password that satisfies the password policy (used for one-time temporary passwords)."""
    while True:
        pw = secrets.token_urlsafe(18)
        if password_policy_ok(pw):
            return pw


def tenant_active(db: Session, actor: Actor) -> bool:
    """Platform staff have no tenant; a tenant actor may act only while its organisation is ACTIVE.
    Callers stamp the actor's tenant scope first (org_tenant is tenant-scoped under RLS)."""
    if not actor.tenant_id:
        return True
    t = db.get(Tenant, actor.tenant_id)
    return t is not None and t.status == "ACTIVE"


# ---------------------------------------------------------------- tokens
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(session: AuthSession) -> str:
    body = json.dumps({"sid": session.session_id, "sub": session.actor_id,
                       "exp": int(ensure_utc(session.expires_at).timestamp())}, separators=(",", ":")).encode()
    sig = hmac.new(_session_key(), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(sig)}"


def resolve_token(db: Session, token: str) -> tuple[Actor, AuthSession]:
    try:
        body_s, sig_s = token.split(".")
        body = _unb64(body_s)
        if not hmac.compare_digest(_unb64(sig_s), hmac.new(_session_key(), body, hashlib.sha256).digest()):
            raise ValueError
        claims = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid session token")
    now = datetime.now(timezone.utc)
    if claims.get("exp", 0) < now.timestamp():
        raise HTTPException(401, "Session expired; sign in again")
    sess = db.get(AuthSession, claims.get("sid"))
    if not sess or sess.revoked_at or ensure_utc(sess.expires_at) < now or sess.actor_id != claims.get("sub"):
        raise HTTPException(401, "Session is no longer valid; sign in again")
    actor = db.get(Actor, sess.actor_id)
    if not actor:
        raise HTTPException(401, "Unknown actor")
    set_tenant_scope(db, actor.tenant_id)
    if not tenant_active(db, actor):
        raise HTTPException(401, "Your organisation's access is suspended")
    return actor, sess


def bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


# ---------------------------------------------------------------- demo credentials
def ensure_demo_credentials(db: Session) -> None:
    """Demo mode only: give each interactive seeded actor the locally configured demo password (must change on first
    use is not forced so the demo stays usable; production disables ZA_DEMO_MODE and provisions via SCIM/IdP)."""
    if not DEMO_MODE:
        return
    if not DEMO_PASSWORD:
        logging.getLogger(__name__).warning("ZA_DEMO_MODE=1 but ZA_DEMO_PASSWORD is not set: no demo credentials created")
        return
    for a in db.execute(select(Actor)).scalars():
        if a.actor_type in INTERACTIVE_TYPES and not db.get(AuthCredential, a.actor_id):
            db.add(AuthCredential(actor_id=a.actor_id, password_hash=hash_password(DEMO_PASSWORD)))
    db.commit()


# ---------------------------------------------------------------- API
router = APIRouter(prefix="/auth")


class LoginIn(BaseModel):
    email: str
    password: str


def _actor_view(db: Session, a: Actor) -> dict:
    from .security import ROLE_PERMISSIONS
    d = to_dict(a)
    t = db.get(Tenant, a.tenant_id) if a.tenant_id else None
    d["tenant_name"] = t.legal_name if t else "Zoiko content/platform operations"
    d["permissions"] = sorted({p for r in a.roles or [] for p in ROLE_PERMISSIONS.get(r, set())})
    cred = db.get(AuthCredential, a.actor_id)
    d["must_change_password"] = bool(cred and cred.must_change)
    return d


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    generic = HTTPException(401, "Email or password is incorrect")
    actor = db.execute(select(Actor).where(Actor.email == body.email.strip().lower())).scalars().first()
    cred = db.get(AuthCredential, actor.actor_id) if actor else None
    now = datetime.now(timezone.utc)
    if not actor or not cred or actor.actor_type not in INTERACTIVE_TYPES:
        check_password(body.password, _DUMMY_HASH)
        raise generic
    set_tenant_scope(db, actor.tenant_id)  # audit rows are tenant-scoped under RLS
    if cred.locked_until and ensure_utc(cred.locked_until) > now:
        raise HTTPException(423, f"Too many failed attempts. Try again after {ensure_utc(cred.locked_until).strftime('%H:%M UTC')}.")
    if not check_password(body.password, cred.password_hash):
        cred.failed_attempts += 1
        if cred.failed_attempts >= MAX_FAILURES:
            cred.locked_until, cred.failed_attempts = now + timedelta(minutes=LOCK_MINUTES), 0
        audit(db, action="auth.login_failed", object_type="Actor", object_id=actor.actor_id, actor=actor,
              tenant_id=actor.tenant_id)
        db.commit()
        raise generic
    cred.failed_attempts, cred.locked_until = 0, None
    if not tenant_active(db, actor):
        audit(db, action="auth.login_refused_suspended", object_type="Actor", object_id=actor.actor_id, actor=actor,
              tenant_id=actor.tenant_id)
        db.commit()
        raise HTTPException(403, "Sign-in is disabled because this organisation is suspended")
    sess = AuthSession(actor_id=actor.actor_id, expires_at=now + timedelta(hours=SESSION_HOURS),
                       user_agent=(request.headers.get("user-agent") or "")[:300])
    db.add(sess)
    db.flush()
    audit(db, action="auth.login", object_type="Actor", object_id=actor.actor_id, actor=actor, tenant_id=actor.tenant_id)
    db.commit()
    return {"token": issue_token(sess), "expires_at": sess.expires_at, "actor": _actor_view(db, actor),
            "must_change_password": cred.must_change}


@router.post("/logout")
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    tok = bearer(authorization)
    if not tok:
        return {"ok": True}
    try:
        actor, sess = resolve_token(db, tok)
    except HTTPException:
        return {"ok": True}  # already invalid — logout is idempotent
    sess.revoked_at = datetime.now(timezone.utc)
    set_tenant_scope(db, actor.tenant_id)
    audit(db, action="auth.logout", object_type="Actor", object_id=actor.actor_id, actor=actor, tenant_id=actor.tenant_id)
    db.commit()
    return {"ok": True}


@router.get("/session")
def session(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    tok = bearer(authorization)
    if not tok:
        raise HTTPException(401, "Not signed in")
    actor, sess = resolve_token(db, tok)
    return {"actor": _actor_view(db, actor), "expires_at": sess.expires_at}


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/password")
def change_password(body: PasswordIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    tok = bearer(authorization)
    if not tok:
        raise HTTPException(401, "Not signed in")
    actor, sess = resolve_token(db, tok)
    set_tenant_scope(db, actor.tenant_id)
    cred = db.get(AuthCredential, actor.actor_id)
    if not cred or not check_password(body.current_password, cred.password_hash):
        raise HTTPException(403, "Current password is incorrect")
    pw = body.new_password
    if not password_policy_ok(pw):
        raise HTTPException(422, f"Use at least {MIN_PASSWORD} characters with upper- and lower-case letters and a digit")
    if check_password(pw, cred.password_hash):
        raise HTTPException(422, "Choose a new password that differs from the current one")
    cred.password_hash, cred.must_change, cred.updated_at = hash_password(pw), False, datetime.now(timezone.utc)
    # revoke every other session for this actor
    for s in db.execute(select(AuthSession).where(AuthSession.actor_id == actor.actor_id,
                                                  AuthSession.revoked_at.is_(None),
                                                  AuthSession.session_id != sess.session_id)).scalars():
        s.revoked_at = datetime.now(timezone.utc)
    audit(db, action="auth.password_changed", object_type="Actor", object_id=actor.actor_id, actor=actor,
          tenant_id=actor.tenant_id)
    db.commit()
    return {"ok": True}


@router.get("/demo-accounts")
def demo_accounts(db: Session = Depends(get_db)):
    """Demo convenience for the sign-in screen. Disabled (404) unless ZA_DEMO_MODE=1."""
    if not DEMO_MODE:
        raise HTTPException(404)
    out = []
    for a in db.execute(select(Actor)).scalars():
        if a.actor_type in INTERACTIVE_TYPES and db.get(AuthCredential, a.actor_id):
            t = db.get(Tenant, a.tenant_id) if a.tenant_id else None
            out.append({"email": a.email, "display_name": a.display_name, "roles": a.roles, "actor_type": a.actor_type,
                        "tenant_name": t.legal_name if t else "Zoiko content operations"})
    return {"accounts": out}  # never return a password
