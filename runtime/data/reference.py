"""In-memory reference data: hospitals (`projects`) and places (`locations`).

The tables are small (tens of hospitals, hundreds of districts), so they are loaded once and
searched in memory — sub-millisecond, no DB round trip during a call. Postgres stays the source
of truth; call `reload()` after data changes. (Switch to SQL search with pgvector / pg_trgm
if locations grow beyond ~50k rows.)
"""

import json
from dataclasses import dataclass, field

import numpy as np

from .db import get_pool


@dataclass(frozen=True)
class Project:
    reference_id: int
    project_name: str
    ar_name: str | None
    city_en: str | None
    city_ar: str | None
    latitude: float | None
    longitude: float | None
    aliases: tuple[str, ...]
    base_extension: str | None = None


@dataclass(frozen=True)
class Location:
    city_en: str | None
    city_ar: str | None
    district_en: str | None
    district_ar: str | None
    latitude: float
    longitude: float


@dataclass
class ReferenceData:
    projects: list[Project] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    embeddings: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float32))  # L2-normalized rows

    @classmethod
    async def load(cls) -> "ReferenceData":
        pool = await get_pool()
        async with pool.acquire() as conn:
            prows = await conn.fetch("""
                SELECT reference_id, project_name, ar_name, city_name_en, city_name_ar,
                       latitude, longitude, aliases::text AS aliases, base_extension
                FROM projects WHERE active AND reference_id IS NOT NULL ORDER BY project_name""")
            lrows = await conn.fetch("""
                SELECT city_name_en, city_name_ar, district_name_en, district_name_ar,
                       latitude, longitude, embedding::text AS embedding
                FROM locations
                WHERE active AND latitude IS NOT NULL AND longitude IS NOT NULL AND embedding IS NOT NULL""")
        projects = [Project(r["reference_id"], r["project_name"], r["ar_name"], r["city_name_en"],
                            r["city_name_ar"], r["latitude"], r["longitude"],
                            tuple(json.loads(r["aliases"]) if r["aliases"] else ()),
                            (r["base_extension"] or "").strip() or None) for r in prows]
        locations = [Location(r["city_name_en"], r["city_name_ar"], r["district_name_en"], r["district_name_ar"],
                              r["latitude"], r["longitude"]) for r in lrows]
        emb = np.array([json.loads(r["embedding"]) for r in lrows], dtype=np.float32)
        if len(emb):
            emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        return cls(projects, locations, emb)


def projects_by_extension(ref: ReferenceData, number: str) -> list[Project]:
    """PBX extension → branch(es): match the leading 4 digits to projects.base_extension, else the leading 3
    (a few branches are registered under a 3-digit prefix). Digits after the prefix are a PBX sub-code."""
    digits = "".join(ch for ch in number if ch.isdigit())
    for n in (4, 3):
        if len(digits) >= n:
            found = [p for p in ref.projects if p.base_extension == digits[:n]]
            if found:
                return found
    return []


_ref: ReferenceData | None = None


async def get_reference() -> ReferenceData:
    global _ref
    if _ref is None:
        _ref = await ReferenceData.load()
    return _ref


async def reload() -> ReferenceData:
    global _ref
    _ref = await ReferenceData.load()
    return _ref


def set_reference(ref: ReferenceData) -> None:
    """For tests / offline evals."""
    global _ref
    _ref = ref
