"""Console sign-in: users, sessions, project roles (Hamsa-style projects with members and invitations).

Who is calling a console API (set per request by `require_console`, read with `principal()`):
  • a signed-in user — bearer token from /api/auth/login (sessions last 14 days; only a hash is stored), who sees
    only the projects they are a member of (owner / admin);
  • the CONSOLE_TOKEN from .env — scripts and automation, every project;
  • nobody, while no user account exists yet and no CONSOLE_TOKEN is set (development) — every project. The console
    then asks for the first account, which becomes owner of every existing project.
"""

import hashlib
import hmac
import logging
import secrets
import smtplib
import ssl
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

log = logging.getLogger(__name__)

SESSION_DAYS = 14
INVITE_DAYS = 7
ROLES = ("owner", "admin")


@dataclass
class Principal:
    kind: str                                  # user | token | open
    user: dict[str, Any] | None = None
    memberships: dict[str, dict] = field(default_factory=dict)   # project → membership row

    @property
    def is_user(self) -> bool:
        return self.kind == "user"

    @property
    def actor(self) -> str:
        return (self.user or {}).get("email") or ("console token" if self.kind == "token" else "console")

    def role(self, ws: str) -> str | None:
        """owner / admin of `ws`; the token and the open console act as owner everywhere."""
        if not self.is_user:
            return "owner"
        m = self.memberships.get(ws)
        return m["role"] if m else None


_PRINCIPAL: ContextVar[Principal] = ContextVar("principal", default=Principal("open"))


def principal() -> Principal:
    return _PRINCIPAL.get()


def set_principal(p: Principal) -> None:
    _PRINCIPAL.set(p)


# ---------------------------------------------------------------- secrets: passwords, tokens

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), digest)
    except Exception:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def now() -> datetime:
    return datetime.now(timezone.utc)


def check_new_password(password: str) -> str | None:
    return "password: at least 8 characters" if len(password or "") < 8 else None


def public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u.get("name") or u["email"].split("@")[0],
            "default_project": u.get("default_project")}


async def start_session(store, user: dict) -> str:
    token = new_token("s")
    await store.create_session(token_hash(token), user["id"], now() + timedelta(days=SESSION_DAYS))
    await store.update_user(user["id"], last_login_at=now())
    return token


async def create_user(store, email: str, name: str, password: str) -> dict:
    row = {"id": uuid.uuid4().hex[:16], "email": email.strip().lower(), "name": name.strip(),
           "password_hash": hash_password(password)}
    await store.create_user(row)
    return await store.user(row["id"])


async def resolve(store, settings, bearer: str | None) -> Principal | None:
    """Who presents `bearer`; None = not allowed in."""
    token = settings.console_token.get_secret_value() if settings.console_token else None
    if token and bearer and hmac.compare_digest(bearer, token):
        return Principal("token")
    if store is not None and bearer and bearer.startswith("s_"):
        user = await store.session_user(token_hash(bearer))
        if user is not None:
            ms = {m["workspace_id"]: m for m in await store.memberships(user["id"])}
            return Principal("user", user, ms)
    if token is None and (store is None or await store.count_users() == 0):
        return Principal("open")
    return None


# ---------------------------------------------------------------- invitation email (optional: SMTP_* in .env)

def mail_configured(settings) -> bool:
    return bool(getattr(settings, "smtp_host", None) and getattr(settings, "smtp_from", None))


def send_invitation_email(settings, to: str, project: str, inviter: str, link: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"{inviter} invited you to the project {project}"
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(f"{inviter} invited you to join the project \"{project}\" on the voice agent console.\n\n"
                    f"Accept the invitation: {link}\n\nThe link is valid for {INVITE_DAYS} days.")
    password = settings.smtp_password.get_secret_value() if settings.smtp_password else None
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, 465, context=ssl.create_default_context(), timeout=15)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
        server.starttls(context=ssl.create_default_context())
    with server:
        if settings.smtp_user:
            server.login(settings.smtp_user, password or "")
        server.send_message(msg)
