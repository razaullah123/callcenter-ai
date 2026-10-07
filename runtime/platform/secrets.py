"""Secret store: API keys and tokens encrypted in the database, referenced by name.

A provider or MCP server never holds a key itself — its settings hold a reference, {"secret": "GROQ_API_KEY"},
resolved when an agent is loaded. Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) under MASTER_KEY,
the one key that stays in .env; the console only ever sees a name and a hint ("••••2f9a").

Resolution order for a name: the secret store, then a .env setting of that name (during migration), then the
process environment.
"""

import logging
import os
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .store import WORKSPACE, current_project

log = logging.getLogger(__name__)

SECRET_FIELDS = {"api_key", "token", "secret", "password"}
NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class SecretsUnavailable(RuntimeError):
    pass


def generate_master_key() -> str:
    return Fernet.generate_key().decode()


class Cipher:
    def __init__(self, master_key: str | None) -> None:
        self._f = Fernet(master_key.encode()) if master_key else None

    @property
    def available(self) -> bool:
        return self._f is not None

    def encrypt(self, value: str) -> str:
        if self._f is None:
            raise SecretsUnavailable("MASTER_KEY is not set — secrets can't be stored (see .env.example)")
        return self._f.encrypt(value.encode()).decode()

    def encrypt_bytes(self, data: bytes) -> bytes:
        if self._f is None:
            raise SecretsUnavailable("MASTER_KEY is not set — nothing can be encrypted")
        return self._f.encrypt(data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        if self._f is None:
            raise SecretsUnavailable("MASTER_KEY is not set — stored data can't be read")
        try:
            return self._f.decrypt(token)
        except InvalidToken:
            raise SecretsUnavailable("a stored file can't be decrypted — MASTER_KEY changed?") from None

    def decrypt(self, token: str) -> str:
        if self._f is None:
            raise SecretsUnavailable("MASTER_KEY is not set — stored secrets can't be read")
        try:
            return self._f.decrypt(token.encode()).decode()
        except InvalidToken:
            raise SecretsUnavailable("a stored secret can't be decrypted — MASTER_KEY changed?") from None


def hint(value: str) -> str:
    return "••••" + value[-4:] if len(value) >= 8 else "••••"


def is_ref(v: Any) -> bool:
    return isinstance(v, dict) and set(v) == {"secret"} and isinstance(v["secret"], str)


def refs_in(obj: Any) -> set[str]:
    if is_ref(obj):
        return {obj["secret"]}
    if isinstance(obj, dict):
        return set().union(*(refs_in(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(refs_in(v) for v in obj)) if obj else set()
    return set()


def secret_name(owner: str, field: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", f"{owner}_{field}".upper())


def is_secret_field(name: str) -> bool:
    return name in SECRET_FIELDS or name.endswith(("_key", "_token", "_secret", "_password"))


class Secrets:
    """Read / write secrets for a workspace."""

    def __init__(self, store, cipher: Cipher, settings=None) -> None:
        self.store, self.cipher, self.settings = store, cipher, settings

    async def get(self, name: str) -> str | None:
        row = await self.store.secret(current_project(), name) if self.store is not None else None
        if row is not None:
            return self.cipher.decrypt(row["ciphertext"])
        v = getattr(self.settings, name.lower(), None) if self.settings is not None else None
        if v is not None:
            return v.get_secret_value() if hasattr(v, "get_secret_value") else str(v)
        return os.environ.get(name)

    async def put(self, name: str, value: str, by: str) -> None:
        if not NAME.match(name):
            raise ValueError("secret names are UPPER_SNAKE_CASE (letters, digits, _), 2–64 characters")
        if not value:
            raise ValueError("the secret value is empty")
        await self.store.put_secret(current_project(), name, self.cipher.encrypt(value), hint(value), by)

    async def resolve(self, obj: Any) -> Any:
        """A copy of `obj` with every {"secret": name} replaced by its value. Missing → error naming the secret."""
        if is_ref(obj):
            value = await self.get(obj["secret"])
            if value is None:
                raise LookupError(f"secret {obj['secret']} is not set")
            return value
        if isinstance(obj, dict):
            return {k: await self.resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [await self.resolve(v) for v in obj]
        return obj

    async def externalize(self, owner: str, settings: dict[str, Any], by: str,
                          current: dict[str, Any] | None = None) -> dict[str, Any]:
        """Settings coming from the console: plain secret values are stored as secrets and replaced by references
        (a value equal to what `current` already resolves to keeps its existing reference)."""
        current = current or {}
        out = {}
        for k, v in settings.items():
            if k == "headers" and isinstance(v, dict):
                out[k] = {h: await self._one(f"{owner}_HEADER_{h}", hv, by, (current.get(k) or {}).get(h))
                          for h, hv in v.items()}
            elif is_secret_field(k):
                out[k] = await self._one(f"{owner}_{k}", v, by, current.get(k))
            else:
                out[k] = v
        return out

    async def _one(self, name: str, value: Any, by: str, current: Any) -> Any:
        if value in (None, "") or is_ref(value):
            return value
        if is_ref(current) and await self.get(current["secret"]) == value:
            return current                               # unchanged: keep the reference
        name = re.sub(r"[^A-Z0-9_]", "_", name.upper())[:64]
        await self.put(name, str(value), by)
        return {"secret": name}
