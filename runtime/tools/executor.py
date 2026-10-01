"""Tool executor: the single path by which the LLM's tool calls reach MCP or local tools.

For every call it:
  1. checks the tool exists and is allowed in the active skill
  2. injects session parameters (patient id, language id) the LLM never sees
  3. coerces + validates arguments against the tool's JSON schema
  4. runs pre-hooks (policy: auth gate, confirm-before-write, argument normalization)
  5. serves cached reads, joins an identical in-flight call (e.g. a prefetch), de-duplicates idempotent writes
  6. executes with a timeout (reads retried once on transport errors; writes never retried)
  7. runs post-hooks (session updates, PHI hiding), summarizes the result for the LLM, emits tool.* events
Errors never raise to the caller; they come back as a ToolResult the LLM can react to.
"""

import asyncio
import copy
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import jsonschema

from runtime.events import BoundEmitter, EventType, Level
from runtime.providers import ToolCall

from .catalog import Catalog
from .local import call_local
from .mcp_client import MCPBackend
from .summarize import to_llm_content
from .types import ToolContext, ToolDef, ToolError, ToolResult

PreHook = Callable[[ToolDef, dict[str, Any], ToolContext], Awaitable[dict[str, Any] | None]]
PostHook = Callable[[ToolDef, dict[str, Any], ToolResult, ToolContext], Awaitable[None]]


class ToolExecutor:
    def __init__(self, catalog: Catalog, mcp: MCPBackend) -> None:
        self.catalog = catalog
        self.mcp = mcp
        self.pre_hooks: list[PreHook] = []     # may return replacement args; raise ToolError to block
        self.post_hooks: list[PostHook] = []   # may rewrite result.content / update session
        self._cache: dict[str, tuple[float, Any]] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._idempotent: dict[str, ToolResult] = {}
        self._llm_specs: dict[str, dict[str, Any]] = {}
        self._background: set[asyncio.Task] = set()

    # ---------- what the LLM sees ----------

    def llm_tools(self, names: list[str] | set[str] | None = None) -> list[dict[str, Any]]:
        """OpenAI-format tool specs with injected parameters removed."""
        selected = self.catalog.tools.values() if names is None else \
            [self.catalog.tools[n] for n in sorted(names) if n in self.catalog.tools]
        return [self._llm_spec(t) for t in selected]

    def _llm_spec(self, tool: ToolDef) -> dict[str, Any]:
        if tool.name not in self._llm_specs:
            schema = copy.deepcopy(tool.input_schema)
            props = schema.get("properties", {})
            hidden = [p for p in props if self.catalog.injected_param(p)]
            for p in hidden:
                props.pop(p)
            if "required" in schema:
                schema["required"] = [r for r in schema["required"] if r not in hidden]
            self._llm_specs[tool.name] = {"type": "function", "function": {
                "name": tool.name, "description": tool.description[:1024], "parameters": schema}}
        return self._llm_specs[tool.name]

    # ---------- execution ----------

    async def execute(self, call: ToolCall, ctx: ToolContext, emitter: BoundEmitter) -> ToolResult:
        t0 = time.perf_counter()
        tool = self.catalog.get(call.name)
        emitter.emit(EventType.TOOL_START, tool=call.name, args=call.arguments)
        args: dict[str, Any] = {}
        try:
            if tool is None:
                raise ToolError(f"unknown tool {call.name}")
            if ctx.allowed_tools is not None and tool.name not in ctx.allowed_tools:
                raise ToolError(f"tool {tool.name} is not available in the current step")
            if "__invalid_json__" in call.arguments:
                raise ToolError("arguments were not valid JSON; call the tool again with a JSON object")
            args = dict(call.arguments)
            for hook in self.pre_hooks:
                args = (await hook(tool, args, ctx)) or args
            args = self._prepare_args(tool, args, ctx)
            result = await self._run(tool, args, ctx)
            for hook in self.post_hooks:
                await hook(tool, args, result, ctx)
        except ToolError as e:
            result = ToolResult(ok=False, error=str(e), content=json.dumps({"error": str(e)}, ensure_ascii=False))
        except asyncio.TimeoutError:
            msg = "the hospital system did not respond in time"
            result = ToolResult(ok=False, error="timeout", content=json.dumps({"error": msg}))
        except Exception as e:  # unexpected: log it, tell the LLM the system failed
            result = ToolResult(ok=False, error=repr(e),
                                content=json.dumps({"error": "the hospital system returned an error"}))
        result.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        if result.ok:
            emitter.emit(EventType.TOOL_END, tool=call.name, latency_ms=result.latency_ms, cached=result.cached,
                         result_chars=len(result.content))
        else:
            emitter.emit(EventType.TOOL_ERROR, level=Level.WARNING, tool=call.name, latency_ms=result.latency_ms,
                         error=result.error)
        return result

    def prefetch(self, name: str, args: dict[str, Any], ctx: ToolContext, emitter: BoundEmitter) -> None:
        """Warm the cache in the background for a read the LLM is likely to request next."""
        tool = self.catalog.get(name)
        if tool is None or tool.kind != "read":
            return
        try:
            prepared = self._prepare_args(tool, dict(args), ctx)
        except ToolError:
            return
        key = self._key(tool, prepared)
        if key in self._inflight or self._cached(tool, key) is not None:
            return

        async def run() -> None:
            t0 = time.perf_counter()
            try:
                await self._run(tool, prepared, ctx)
                emitter.emit(EventType.TOOL_END, tool=name, prefetch=True,
                             latency_ms=round((time.perf_counter() - t0) * 1000, 1))
            except Exception as e:
                emitter.emit(EventType.TOOL_ERROR, level=Level.DEBUG, tool=name, prefetch=True, error=repr(e))

        task = asyncio.create_task(run(), name=f"prefetch:{name}")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def ready(self, name: str, args: dict[str, Any], ctx: ToolContext) -> bool:
        """True if this read would be answered from cache right now (no waiting)."""
        tool = self.catalog.get(name)
        if tool is None:
            return False
        try:
            prepared = self._prepare_args(tool, dict(args), ctx)
        except ToolError:
            return False
        return self._cached(tool, self._key(tool, prepared)) is not None

    def _prepare_args(self, tool: ToolDef, llm_args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        schema = tool.input_schema
        props = schema.get("properties", {})
        args = {k: v for k, v in llm_args.items() if not self.catalog.injected_param(k)}  # LLM can't override
        for param in props:
            session_field = self.catalog.injected_param(param)
            if session_field == "language_id":
                args[param] = ctx.language_id
            elif session_field == "patient_id" and ctx.patient_id is not None:
                args[param] = ctx.patient_id
        missing_injected = [p for p in schema.get("required", [])
                            if self.catalog.injected_param(p) == "patient_id" and p not in args]
        if missing_injected:
            raise ToolError("the caller is not verified yet; verify the caller first")
        args = _coerce(args, props)
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as e:
            raise ToolError(f"invalid arguments: {e.message}") from None
        return args

    def _key(self, tool: ToolDef, args: dict[str, Any]) -> str:
        """Canonical key: parameters equal to their defaults (and page 1) don't change the result."""
        props = tool.input_schema.get("properties", {})
        canon = {k: v for k, v in args.items()
                 if not ("default" in props.get(k, {}) and props[k]["default"] == v) and not (k == "page" and v == 1)}
        return f"{tool.name}:{json.dumps(canon, sort_keys=True, ensure_ascii=False)}"

    def _cached(self, tool: ToolDef, key: str) -> Any | None:
        if tool.kind == "read" and tool.cache_ttl:
            hit = self._cache.get(key)
            if hit and hit[0] > time.monotonic():
                return hit
        return None

    async def _run(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        key = self._key(tool, args)
        if hit := self._cached(tool, key):
            return ToolResult(ok=True, data=hit[1], content=to_llm_content(tool.name, hit[1]), cached=True)
        idem_key = hashlib.sha256(f"{ctx.call_id}|{key}".encode()).hexdigest() if tool.idempotent else None
        if idem_key and idem_key in self._idempotent:
            prev = self._idempotent[idem_key]
            return ToolResult(ok=prev.ok, data=prev.data, content=prev.content, cached=True)
        if tool.kind == "read" and key in self._inflight:          # join a prefetch / concurrent identical read
            ok, data = await asyncio.shield(self._inflight[key])
            return ToolResult(ok=ok, data=data, content=to_llm_content(tool.name, data), cached=True,
                              error=None if ok else _error_text(data))

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        if tool.kind == "read":
            self._inflight[key] = future
        try:
            attempts = 2 if tool.kind == "read" else 1
            for attempt in range(attempts):
                try:
                    ok, data = await asyncio.wait_for(self._dispatch(tool, args, ctx), tool.timeout_s)
                    break
                except (ConnectionError, OSError):
                    if attempt == attempts - 1:
                        raise
            future.set_result((ok, data))
        except BaseException as e:
            if not future.done():
                future.set_exception(e)
                future.exception()  # mark retrieved when nobody joined
            raise
        finally:
            self._inflight.pop(key, None)

        result = ToolResult(ok=ok, data=data, content=to_llm_content(tool.name, data),
                            error=None if ok else _error_text(data))
        if ok and tool.kind == "read" and tool.cache_ttl:
            self._cache[key] = (time.monotonic() + tool.cache_ttl, data)
        if ok and idem_key:
            self._idempotent[idem_key] = result
        return result

    async def _dispatch(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> tuple[bool, Any]:
        if tool.source == "local":
            return True, await call_local(tool.name, args, ctx)
        if tool.source == "http":
            from .http_tool import call_http
            return await call_http(tool.http or {}, args, tool.timeout_s or 10)
        return await self.mcp.call(tool.name, args, tool.timeout_s or 10)


def _coerce(args: dict[str, Any], props: dict[str, Any]) -> dict[str, Any]:
    """Match values to each tool's declared types: LLMs send "15" for integers, and some HIS tools
    declare ids as strings (e.g. patientId) while the session holds integers."""
    out = dict(args)
    for k, v in args.items():
        types_ = props.get(k, {}).get("type")
        types_ = types_ if isinstance(types_, list) else [types_]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and "string" in types_ \
                and not {"integer", "number"} & set(types_):
            out[k] = str(v)
            continue
        if not isinstance(v, str):
            continue
        s = v.strip()
        if "integer" in types_ and s.lstrip("-").isdigit():
            out[k] = int(s)
        elif "number" in types_:
            try:
                out[k] = float(s)
            except ValueError:
                pass
        elif "boolean" in types_ and s.lower() in ("true", "false"):
            out[k] = s.lower() == "true"
    return out


def _error_text(data: Any) -> str:
    return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)[:500]
