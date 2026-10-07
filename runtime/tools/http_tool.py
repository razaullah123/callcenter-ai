"""API-request tools: a tool defined entirely in configuration — an HTTP endpoint the agent can call.

Policy (in the tool library / an agent's release):
    source: http
    description: "Look up a courier shipment by tracking number."
    input_schema: {type: object, properties: {tracking_no: {type: string}}, required: [tracking_no]}
    http:
      method: GET | POST | PUT | PATCH | DELETE
      url: "https://api.example.com/shipments/{tracking_no}"      # {arg} placeholders are filled from the args
      headers: {X-Client: voice-agent}                             # values may be {secret: NAME}
      auth: {type: bearer, token: {secret: COURIER_API_KEY}}       # or token ("Authorization: Token ..."), basic {username, password},
                                                                   # or api_key {header, value}; secrets resolved
                                                                   # when the agent loads
      # remaining args: query string for GET / DELETE, JSON body otherwise

Result: the JSON response (or text); ok = HTTP 2xx and not {"success": false}.

TOOLS_MODE applies to them like to the HIS tools: `mock` calls nothing; `hybrid` calls lookups (read) but only
simulates write / send tools (a booking, an SMS) unless HYBRID_LIVE_BOOKING is set; `live` calls everything.
"""

import base64
import re
from typing import Any
from urllib.parse import quote

import httpx

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(limits=httpx.Limits(max_connections=50))
    return _client


def build_request(spec: dict[str, Any], args: dict[str, Any]) -> tuple[str, str, dict[str, str], dict | None, dict | None]:
    """(method, url, headers, query, json body) for `args` — placeholders consume their args."""
    method = str(spec.get("method") or "GET").upper()
    rest = dict(args)
    url = _PLACEHOLDER.sub(lambda m: quote(str(rest.pop(m.group(1), "")), safe=""), str(spec["url"]))
    headers = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
    headers.update(auth_headers(spec.get("auth")))
    if method in ("GET", "DELETE"):
        return method, url, headers, rest or None, None
    return method, url, headers, None, rest


AUTH_TYPES = ("none", "bearer", "token", "basic", "api_key")


def auth_headers(auth: dict[str, Any] | None) -> dict[str, str]:
    """The header an API tool's `auth` adds (its secrets already resolved to values)."""
    kind = (auth or {}).get("type", "none")
    if kind == "bearer":
        return {"Authorization": f"Bearer {auth.get('token', '')}"}
    if kind == "token":
        return {"Authorization": f"Token {auth.get('token', '')}"}
    if kind == "basic":
        raw = f"{auth.get('username', '')}:{auth.get('password', '')}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
    if kind == "api_key":
        return {str(auth.get("header") or "X-API-Key"): str(auth.get("value", ""))}
    return {}


def auth_errors(auth: Any) -> list[str]:
    if auth is None:
        return []
    if not isinstance(auth, dict) or auth.get("type", "none") not in AUTH_TYPES:
        return [f"http.auth.type must be one of {', '.join(AUTH_TYPES)}"]
    need = {"bearer": ["token"], "token": ["token"], "basic": ["username", "password"], "api_key": ["header", "value"]}.get(auth["type"], [])
    return [f"http.auth.{k} is required for {auth['type']}" for k in need if auth.get(k) in (None, "")]


def simulated(kind: str) -> tuple[bool, Any] | None:
    """A stand-in result when TOOLS_MODE says this tool must not really run; None → call it."""
    from runtime.config import get_settings
    s = get_settings()
    if s.tools_mode == "mock":
        return (True, {"success": True, "simulated": True}) if kind in ("write", "send") else \
            (False, {"success": False, "simulated": True, "message": "TOOLS_MODE=mock: API tools are not called"})
    if s.tools_mode == "hybrid" and kind in ("write", "send") and not s.hybrid_live_booking:
        return True, {"success": True, "simulated": True, "message": "simulated (TOOLS_MODE=hybrid)"}
    return None


async def call_http(spec: dict[str, Any], args: dict[str, Any], timeout_s: float) -> tuple[bool, Any]:
    method, url, headers, query, body = build_request(spec, args)
    resp = await _http().request(method, url, headers=headers, params=query, json=body, timeout=timeout_s)
    try:
        data: Any = resp.json()
    except ValueError:
        data = resp.text[:4000]
    ok = resp.is_success and not (isinstance(data, dict) and data.get("success") is False)
    if not resp.is_success and isinstance(data, str):
        data = {"status": resp.status_code, "error": data[:500]}
    return ok, data
