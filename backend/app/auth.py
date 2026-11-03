"""Interactive authentication: password sign-in, signed session tokens, logout (ZA-IAM-001 baseline).

* Passwords are hashed with scrypt (stdlib) and a per-credential salt; comparison is constant-time.
* A session token is 32 random bytes, returned once at sign-in. The server-side ``AuthSession`` row keeps only its
  SHA-256 (``token_hash``), so neither the database nor any key on disk can re-create a live token, and logout or
  revocation takes effect on the next request (ZA-IAM-001 §05, §22).
* Only HUMAN and EXTERNAL_AUDITOR actors may sign in interactively; agents and services never
  hold passwords (INV-01 — they cannot become the accountable authoriser by logging in).
* Repeated failures lock the credential temporarily. Unknown, non-interactive, locked and wrong-password attempts get
  the same answer after the same work, so neither the message nor the timing reveals whether an account exists or is
  locked (ZA-IAM-001 §03); the reason class goes to the security audit only (§21 identity.authentication_failed).

Production replaces this module with OIDC/SAML federation + SCIM; the ``Ctx`` contract is unchanged.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import capability, get_db, set_principal, set_system_scope, set_tenant_scope
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
TOKEN_BYTES = 32


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
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(session: AuthSession) -> str:
    """A new bearer token for `session`, which keeps only its hash. Call before the session row is committed; the token
    is returned to the caller once and is never stored or logged."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    session.token_hash = token_hash(token)
    return token


def resolve_token(db: Session, token: str) -> tuple[Actor, AuthSession]:
    """The live session a bearer token names, and its actor; the database context then names that principal. Call inside
    ``capability(db, "auth")``: the identity tables are unreachable otherwise (migration 0007)."""
    if not token or len(token) > 128:
        raise HTTPException(401, "Invalid session token")
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash(token))).scalars().first()
    if sess is None:
        raise HTTPException(401, "Invalid session token")
    if sess.revoked_at or ensure_utc(sess.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(401, "Session is no longer valid; sign in again")
    actor = db.get(Actor, sess.actor_id)
    if not actor:
        raise HTTPException(401, "Unknown actor")
    set_principal(db, actor.tenant_id, actor.actor_id, sess.session_id)
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
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    email: str
    password: str


def _actor_view(db: Session, a: Actor) -> dict:
    """The signed-in principal, with its roles and permissions read from the role registry (Stage 4b), not from the
    org_actor.roles cache. `permissions` are those in force organisation- or platform-wide; entity-scoped ones are
    listed under role_assignments."""
    from .iam import active_assignments, applies, assignment_view, wide_roles
    from .security import ROLE_PERMISSIONS
    grants = active_assignments(db, a)
    d = to_dict(a)
    t = db.get(Tenant, a.tenant_id) if a.tenant_id else None
    d["tenant_name"] = t.legal_name if t else "Zoiko content/platform operations"
    d["roles"] = wide_roles(grants)
    d["permissions"] = sorted({p for g in grants if applies(g, None) for p in ROLE_PERMISSIONS.get(g.role_code, set())})
    d["role_assignments"] = [assignment_view(g) for g in grants]
    cred = db.get(AuthCredential, a.actor_id)
    d["must_change_password"] = bool(cred and cred.must_change)
    return d


def _attempt_ref(email: str) -> str:
    """Pseudonymous correlation of attempts on an identifier that matches no account; the identifier is not stored."""
    return "email-sha256:" + hashlib.sha256(email.encode()).hexdigest()[:32]


def _login_failed(db: Session, reason_class: str, *, actor: Actor | None = None, email: str = "",
                  locked: bool = False) -> HTTPException:
    """Record ZA-IAM-001 §21 identity.authentication_failed (as ``auth.login_failed``; reason class only, never the
    password) and return the single generic answer every failed sign-in gets."""
    audit(db, action="auth.login_failed", object_type="Actor" if actor else "LoginAttempt",
          object_id=actor.actor_id if actor else _attempt_ref(email), actor=actor,
          tenant_id=actor.tenant_id if actor else None, new="LOCKED" if locked else None, reason=reason_class)
    db.commit()
    return HTTPException(401, "Email or password is incorrect")


# Every endpoint here reads or writes the identity tables, so each runs under the signed `auth` capability; nothing
# else in the API holds it (migration 0007).
@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    with capability(db, "auth"):
        return _login(body, request, db)


def _login(body: LoginIn, request: Request, db: Session):
    email = body.email.strip().lower()
    actor = db.execute(select(Actor).where(Actor.email == email)).scalars().first()
    now = datetime.now(timezone.utc)
    if not actor:
        check_password(body.password, _DUMMY_HASH)
        raise _login_failed(db, "UNKNOWN_ACCOUNT", email=email)
    set_tenant_scope(db, actor.tenant_id)  # audit rows are tenant-scoped under RLS
    # One attempt at a time per credential: concurrent guesses cannot outrun the failure counter.
    cred = db.get(AuthCredential, actor.actor_id, with_for_update=True)
    if not cred or actor.actor_type not in INTERACTIVE_TYPES:
        check_password(body.password, _DUMMY_HASH)
        raise _login_failed(db, "NOT_INTERACTIVE" if actor.actor_type not in INTERACTIVE_TYPES else "NO_CREDENTIAL",
                            actor=actor)
    if cred.locked_until and ensure_utc(cred.locked_until) > now:
        check_password(body.password, _DUMMY_HASH)  # same answer and the same work as a wrong password
        raise _login_failed(db, "LOCKED", actor=actor)
    if not check_password(body.password, cred.password_hash):
        cred.failed_attempts += 1
        locked = cred.failed_attempts >= MAX_FAILURES
        if locked:
            cred.locked_until, cred.failed_attempts = now + timedelta(minutes=LOCK_MINUTES), 0
        raise _login_failed(db, "BAD_PASSWORD", actor=actor, locked=locked)
    cred.failed_attempts, cred.locked_until = 0, None
    if not tenant_active(db, actor):
        audit(db, action="auth.login_refused_suspended", object_type="Actor", object_id=actor.actor_id, actor=actor,
              tenant_id=actor.tenant_id)
        db.commit()
        raise HTTPException(403, "Sign-in is disabled because this organisation is suspended")
    # A new session (and session ID) on every sign-in: nothing the client presents is reused (ZA-IAM-001 §03 fixation).
    sess = AuthSession(actor_id=actor.actor_id, expires_at=now + timedelta(hours=SESSION_HOURS),
                       user_agent=(request.headers.get("user-agent") or "")[:300])
    token = issue_token(sess)
    db.add(sess)
    db.flush()
    set_principal(db, actor.tenant_id, actor.actor_id, sess.session_id)  # authenticated: its own registry rows are readable
    audit(db, action="auth.login", object_type="Actor", object_id=actor.actor_id, actor=actor, tenant_id=actor.tenant_id)
    db.commit()
    return {"token": token, "expires_at": sess.expires_at, "actor": _actor_view(db, actor),
            "must_change_password": cred.must_change}


@router.post("/logout")
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    with capability(db, "auth"):
        return _logout(authorization, db)


def _logout(authorization: str | None, db: Session):
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
    with capability(db, "auth"):
        actor, sess = resolve_token(db, tok)
        return {"actor": _actor_view(db, actor), "expires_at": sess.expires_at}


class PasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    current_password: str
    new_password: str


@router.post("/password")
def change_password(body: PasswordIn, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    with capability(db, "auth"):
        return _change_password(body, authorization, db)


def _change_password(body: PasswordIn, authorization: str | None, db: Session):
    tok = bearer(authorization)
    if not tok:
        raise HTTPException(401, "Not signed in")
    actor, sess = resolve_token(db, tok)
    set_tenant_scope(db, actor.tenant_id)
    # Locked first, so two concurrent changes for one account cannot interleave their session revocations.
    cred = db.get(AuthCredential, actor.actor_id, with_for_update=True)
    if not cred or not check_password(body.current_password, cred.password_hash):
        audit(db, action="auth.password_change_failed", object_type="Actor", object_id=actor.actor_id, actor=actor,
              tenant_id=actor.tenant_id, reason="BAD_CURRENT_PASSWORD")
        db.commit()
        raise HTTPException(403, "Current password is incorrect")
    pw = body.new_password
    if not password_policy_ok(pw):
        raise HTTPException(422, f"Use at least {MIN_PASSWORD} characters with upper- and lower-case letters and a digit")
    if check_password(pw, cred.password_hash):
        raise HTTPException(422, "Choose a new password that differs from the current one")
    cred.password_hash, cred.must_change, cred.updated_at = hash_password(pw), False, datetime.now(timezone.utc)
    # revoke every other session for this actor
    revoked = 0
    for s in db.execute(select(AuthSession).where(AuthSession.actor_id == actor.actor_id,
                                                  AuthSession.revoked_at.is_(None),
                                                  AuthSession.session_id != sess.session_id)).scalars():
        s.revoked_at, revoked = datetime.now(timezone.utc), revoked + 1
    audit(db, action="auth.password_changed", object_type="Actor", object_id=actor.actor_id, actor=actor,
          tenant_id=actor.tenant_id, reason=f"{revoked} other session(s) revoked")
    db.commit()
    return {"ok": True}


@router.get("/demo-accounts")
def demo_accounts(db: Session = Depends(get_db)):
    """Demo convenience for the sign-in screen. Disabled (404) unless ZA_DEMO_MODE=1."""
    if not DEMO_MODE:
        raise HTTPException(404)
    out = []
    set_system_scope(db, True)  # demo mode only: organisation names across the synthetic fixture
    with capability(db, "auth"):
        for a in db.execute(select(Actor)).scalars():
            if a.actor_type in INTERACTIVE_TYPES and db.get(AuthCredential, a.actor_id):
                t = db.get(Tenant, a.tenant_id) if a.tenant_id else None
                out.append({"email": a.email, "display_name": a.display_name, "roles": a.roles,
                            "actor_type": a.actor_type, "tenant_name": t.legal_name if t else "Zoiko content operations"})
    return {"accounts": out}  # never return a password
