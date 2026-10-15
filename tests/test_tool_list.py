"""Hamsa's shape of the tool list: GET /api/voice-agents/web-tool/list?projectId=&skip=&take=."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .test_studio import studio  # noqa: F401  (fixture)


@pytest.fixture
def client(studio):  # noqa: F811
    c, rt, store, loop = studio
    from runtime.control import tools_api
    c.app.include_router(tools_api.router)
    return c, rt, store, loop


def test_list_has_hamsas_envelope_and_item_fields(client):
    c, rt, store, loop = client
    total = len(loop.run_until_complete(store.tools("hmg")))
    r = c.get("/api/voice-agents/web-tool/list?projectId=hmg&skip=1&take=10").json()
    assert r["success"] is True and r["message"] == "success"
    d = r["data"]
    assert d["total"] == total and len(d["items"]) == d["filtered"] == min(10, total)
    t = d["items"][0]
    assert set(t) == {"id", "persistentId", "version", "name", "type", "userId", "projectId", "isActive", "async", "description",
                      "collectionId", "toolSettings", "params", "messages"}
    assert set(t["toolSettings"]) == {"httpHeaders", "pathParameters", "serverUrl", "timeout", "authToken", "methodType", "onHoldMusic"}
    assert t["projectId"] == "hmg" and t["type"] in {"FUNCTION", "MCP", "LOCAL", "WEB"} and t["params"]["type"] == "object"


def test_skip_is_the_page_number_and_search_filters(client):
    c, rt, store, loop = client
    names = [t["name"] for t in c.get("/api/voice-agents/web-tool/list?projectId=hmg&skip=1&take=100").json()["data"]["items"]]
    assert names == sorted(names, key=str.lower) and len(names) > 3
    one = lambda page: c.get(f"/api/voice-agents/web-tool/list?projectId=hmg&skip={page}&take=1").json()["data"]["items"][0]["name"]  # noqa: E731
    assert [one(1), one(2), one(3)] == names[:3]
    hit = c.get(f"/api/voice-agents/web-tool/list?projectId=hmg&search={names[1][:6]}").json()["data"]
    assert names[1] in [t["name"] for t in hit["items"]]
    assert c.get("/api/voice-agents/web-tool/list?projectId=nope").status_code in (401, 404)
    cols = c.get("/api/voice-agents/collections/list?projectId=hmg").json()
    assert cols["success"] and cols["data"]["total"] == len(cols["data"]["items"])
