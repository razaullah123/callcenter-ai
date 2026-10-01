"""API-request tools: a tool defined entirely in configuration — an HTTP endpoint the agent can call.

Policy (in the tool library / an agent's release):
    source: http
    description: "Look up a courier shipment by tracking number."
    input_schema: {type: object, properties: {tracking_no: {type: string}}, required: [tracking_no]}
    http:
      method: GET | POST | PUT | PATCH | DELETE
      url: "https://api.example.com/shipments/{tracking_no}"      # {arg} placeholders are filled from the args
      headers: {Authorization: {secret: COURIER_API_KEY}}          # secrets resolved when the agent loads
      # remaining args: query string for GET / DELETE, JSON body otherwise

Result: the JSON response (or text); ok = HTTP 2xx and not {"success": false}.
"""

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
    if method in ("GET", "DELETE"):
        return method, url, headers, rest or None, None
    return method, url, headers, None, rest


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
