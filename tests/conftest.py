"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def readable_project_ids(monkeypatch):
    """New projects get UUID ids in real life; the tests name them after the project ("clinic", "sara-lab") so they stay
    readable. tests/test_projects.py::test_new_projects_get_uuid_ids checks the real thing."""
    from runtime.control import projects_api

    def slug_id(name: str, existing: set[str]) -> str:
        base = projects_api._slug(name) or "project"
        pid, n = base, 2
        while pid in existing:
            pid, n = f"{base}-{n}", n + 1
        return pid
    monkeypatch.setattr(projects_api, "new_project_id", slug_id)
    from runtime.control import agents_api
    monkeypatch.setattr(agents_api, "new_agent_id", lambda name: agents_api._slug(name))
