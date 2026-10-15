"""Knowledge base API (12.9) — the project's documents and free text, and which agents search them.

    GET    /api/knowledge                     items (+ which agents use them), usage and limits
    POST   /api/knowledge/text                {name, content}           add free text
    POST   /api/knowledge/file                {filename, data (base64), name?}   add a document (PDF, DOCX, TXT, MD,
                                                                         HTML, EPUB) — its text is extracted, the
                                                                         file itself isn't kept
    GET    /api/knowledge/{id}                one item with its text
    DELETE /api/knowledge/{id}                refused (409) while an agent uses the item
    POST   /api/knowledge/url/discover        {url}                     pages the site's sitemap lists (≤ 100)
    POST   /api/knowledge/url                 {name, url, urls?}        add a web page (or the chosen sitemap pages)
    PATCH  /api/knowledge/{id}                {name}                    rename (the text itself can't be edited)
    POST   /api/knowledge/{id}/reprocess      chunk + embed again (e.g. after the embedding service was down)
    POST   /api/knowledge/search              {query, items?}           what an agent would find
    POST   /api/knowledge/use                 {agent, items}            the agent's DRAFT searches these items (live
                                                                         after Publish, through its test checks)
Items are processed in the background: status processing → completed / completed_with_errors / failed.
"""

import asyncio
import base64
import binascii
import copy
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from runtime.control.accounts import principal
from runtime.control.api import _rt, audited, auth
from runtime.platform import current_project
from runtime.platform import knowledge as kb

router = APIRouter(prefix="/api")
_tasks: set[asyncio.Task] = set()


def _store():
    rt = _rt()
    if rt.platform is None:
        raise HTTPException(503, "the platform database is not available")
    return rt, rt.platform


async def _item(store, item_id: str) -> dict:
    item = await store.kb_item(item_id)
    if item is None or item["workspace_id"] != current_project():
        raise HTTPException(404, "knowledge item not found")
    return item


def _process(rt, store, item_id: str, ws: str, text: str) -> None:
    async def run():
        embed = await rt.loader.embedder(ws) if getattr(rt, "loader", None) is not None else None
        await kb.ingest(store, item_id, ws, text, embed)
    task = asyncio.create_task(run(), name=f"kb-{item_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _usage(store, ws: str) -> dict[str, dict[str, list[dict]]]:
    """item id → {"used_by": agents whose published release searches it, "draft_by": agents whose draft adds it}."""
    out: dict[str, dict[str, list[dict]]] = {}
    for a in await store.agents(ws):
        ref = {"agent": a["id"], "name": a["name"]}
        live = []
        if a.get("published_release_id"):
            rel = await store.release(a["published_release_id"])
            live = ((rel or {}).get("bundle", {}).get("knowledge") or {}).get("items") or []
            for i in live:
                out.setdefault(i, {"used_by": [], "draft_by": []})["used_by"].append(ref)
        for i in ((a.get("draft") or {}).get("knowledge") or {}).get("items") or []:
            if i not in live:
                out.setdefault(i, {"used_by": [], "draft_by": []})["draft_by"].append(ref)
    return out


@router.get("/knowledge", dependencies=auth)
async def list_items() -> dict:
    rt, store = _store()
    ws = current_project()
    usage = await _usage(store, ws)
    items = [{**i, **usage.get(i["id"], {"used_by": [], "draft_by": []})} for i in await store.kb_items(ws)]
    return {"items": items, "usage_bytes": await store.kb_usage(ws), "quota_bytes": kb.QUOTA_BYTES,
            "limits": {"file_bytes": kb.MAX_FILE_BYTES, "text_chars": kb.MAX_TEXT_CHARS, "extensions": kb.EXTENSIONS,
                       "urls": kb.MAX_URLS}}


class TextBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=kb.MAX_TEXT_CHARS)


@router.post("/knowledge/text", dependencies=auth)
@audited("knowledge.added")
async def add_text(body: TextBody) -> dict:
    rt, store = _store()
    ws = current_project()
    text = kb.normalize(body.content)
    if not text:
        raise HTTPException(422, "the text is empty")
    size = len(body.content.encode("utf-8"))
    await _check_quota(store, ws, size)
    item_id = "kb_" + secrets.token_hex(6)
    await store.put_kb_item({"id": item_id, "workspace_id": ws, "name": body.name.strip(), "type": "text",
                             "extension": None, "size_bytes": size, "words": kb.words(text), "status": "processing",
                             "content": text, "created_by": principal().actor})
    _process(rt, store, item_id, ws, text)
    return {"id": item_id, "status": "processing"}


class FileBody(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    data: str                                    # base64
    name: str | None = Field(default=None, max_length=200)


@router.post("/knowledge/file", dependencies=auth)
@audited("knowledge.added")
async def add_file(body: FileBody) -> dict:
    rt, store = _store()
    ws = current_project()
    try:
        raw = base64.b64decode(body.data, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "the file data isn't valid base64") from None
    if len(raw) > kb.MAX_FILE_BYTES:
        raise HTTPException(413, f"the file is larger than {kb.MAX_FILE_BYTES // (1024 * 1024)} MB")
    await _check_quota(store, ws, len(raw))
    try:
        text, ext = await asyncio.to_thread(kb.extract_text, body.filename, raw)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except Exception as e:                      # noqa: BLE001 — a broken file
        raise HTTPException(422, f"the file couldn't be read: {str(e)[:200]}") from None
    item_id = "kb_" + secrets.token_hex(6)
    name = (body.name or body.filename).strip()
    await store.put_kb_item({"id": item_id, "workspace_id": ws, "name": name, "type": "file", "extension": ext,
                             "size_bytes": len(raw), "words": kb.words(text), "status": "processing",
                             "content": text, "created_by": principal().actor})
    _process(rt, store, item_id, ws, text)
    return {"id": item_id, "status": "processing", "words": kb.words(text)}


class UrlBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@router.post("/knowledge/url/discover", dependencies=auth)
async def discover_url(body: UrlBody) -> dict:
    _store()
    try:
        return {"urls": await kb.discover_urls(body.url)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


class AddUrlBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    urls: list[str] | None = Field(default=None, max_length=kb.MAX_URLS)    # pages picked from the sitemap


@router.post("/knowledge/url", dependencies=auth)
@audited("knowledge.added")
async def add_url(body: AddUrlBody) -> dict:
    rt, store = _store()
    ws = current_project()
    try:
        pages = [await kb.check_url(u) for u in (body.urls or [body.url])]
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    await _check_quota(store, ws, 0)
    item_id = "kb_" + secrets.token_hex(6)
    await store.put_kb_item({"id": item_id, "workspace_id": ws, "name": body.name.strip(), "type": "url",
                             "extension": None, "source_url": body.url.strip(), "size_bytes": 0, "words": 0,
                             "status": "processing", "content": None, "created_by": principal().actor})

    async def run():
        try:
            text, errors = await kb.fetch_site_text(pages)
        except Exception as e:                               # noqa: BLE001
            await store.put_kb_item({"id": item_id, "status": "failed", "error": str(e)[:500]})
            return
        size = len(text.encode("utf-8"))
        if await store.kb_usage(ws) + size > kb.QUOTA_BYTES:
            await store.put_kb_item({"id": item_id, "status": "failed", "error": "the project's knowledge base is full"})
            return
        await store.put_kb_item({"id": item_id, "content": text, "size_bytes": size, "words": kb.words(text)})
        embed = await rt.loader.embedder(ws) if getattr(rt, "loader", None) is not None else None
        await kb.ingest(store, item_id, ws, text, embed)
        if errors:                                           # some pages failed: usable, but say which
            item = await store.kb_item(item_id) or {}
            note = f"{len(errors)} of {len(pages)} pages could not be read: " + "; ".join(errors)[:400]
            await store.put_kb_item({"id": item_id, "error": ((item.get("error") or "") + " " + note).strip(),
                                     "status": "completed_with_errors" if item.get("status") == "completed" else item.get("status", "failed")})
    task = asyncio.create_task(run(), name=f"kb-url-{item_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"id": item_id, "status": "processing", "pages": len(pages)}


async def _check_quota(store, ws: str, size: int) -> None:
    used = await store.kb_usage(ws)
    if used + size > kb.QUOTA_BYTES:
        raise HTTPException(413, f"the project's knowledge base is full ({used // (1024 * 1024)} of "
                                 f"{kb.QUOTA_BYTES // (1024 * 1024)} MB used)")


@router.get("/knowledge/{item_id}", dependencies=auth)
async def get_item(item_id: str) -> dict:
    rt, store = _store()
    item = await _item(store, item_id)
    return {**item, **(await _usage(store, current_project())).get(item_id, {"used_by": [], "draft_by": []})}


@router.delete("/knowledge/{item_id}", dependencies=auth)
@audited("knowledge.deleted")
async def delete_item(item_id: str) -> dict:
    rt, store = _store()
    item = await _item(store, item_id)
    use = (await _usage(store, current_project())).get(item_id, {})
    if agents := [a["name"] for a in use.get("used_by", []) + use.get("draft_by", [])]:
        raise HTTPException(409, f"{item['name']} is used by {', '.join(dict.fromkeys(agents))} — remove it from "
                                 "the agent(s) first")
    await store.delete_kb_item(item_id)
    return {"deleted": item_id, "name": item["name"]}


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.patch("/knowledge/{item_id}", dependencies=auth)
@audited("knowledge.renamed")
async def rename_item(item_id: str, body: RenameBody) -> dict:
    rt, store = _store()
    await _item(store, item_id)
    if not body.name.strip():
        raise HTTPException(422, "the name is empty")
    await store.put_kb_item({"id": item_id, "name": body.name.strip()})
    return {"id": item_id, "name": body.name.strip()}


@router.post("/knowledge/{item_id}/reprocess", dependencies=auth)
async def reprocess(item_id: str) -> dict:
    rt, store = _store()
    item = await _item(store, item_id)
    if not item.get("content"):
        raise HTTPException(409, "this item has no text to process")
    await store.put_kb_item({"id": item_id, "status": "processing", "error": None})
    _process(rt, store, item_id, item["workspace_id"], item["content"])
    return {"id": item_id, "status": "processing"}


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    items: list[str] | None = None


@router.post("/knowledge/search", dependencies=auth)
async def test_search(body: SearchBody) -> dict:
    rt, store = _store()
    ws = current_project()
    mine = [i["id"] for i in await store.kb_items(ws)]
    items = [i for i in (body.items or mine) if i in mine]
    embed = await rt.loader.embedder(ws) if getattr(rt, "loader", None) is not None else None
    return await kb.search(store, ws, items, body.query, embed, k=5)


class UseBody(BaseModel):
    agent: str
    items: list[str]


@router.post("/knowledge/use", dependencies=auth)
@audited("knowledge.assigned")
async def use_items(body: UseBody) -> dict:
    """The agent's draft searches exactly these items (empty: no knowledge base)."""
    from runtime.control.agents_api import _agent, _check, _working
    rt, store = _store()
    a = await _agent(store, body.agent)
    mine = {i["id"] for i in await store.kb_items(current_project())}
    if unknown := [i for i in body.items if i not in mine]:
        raise HTTPException(404, f"unknown knowledge items: {', '.join(unknown)}")
    bundle = await _working(store, a)
    if body.items:
        bundle["knowledge"] = {"items": list(dict.fromkeys(body.items))}
    else:
        bundle.pop("knowledge", None)
    if errors := await _check(rt, store, body.agent, copy.deepcopy(bundle)):
        raise HTTPException(422, {"errors": errors})
    await store.set_draft(body.agent, bundle, principal().actor)
    return {"agent": body.agent, "items": (bundle.get("knowledge") or {}).get("items", []), "draft": True}
