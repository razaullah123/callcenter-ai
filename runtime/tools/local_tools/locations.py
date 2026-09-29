"""resolve_location — turn the place a caller said into coordinates.

Hybrid search over `locations`:
  • fuzzy name matching (normalized Arabic / English district + city names)
  • semantic similarity with the in-house embedding model (same model + text format as the stored vectors)
fused with reciprocal-rank fusion. A bare city name ("الرياض") resolves to the city centroid.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import lru_cache

import numpy as np
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from runtime.data.reference import Location, ReferenceData, get_reference
from runtime.text.arabic import core_tokens, normalize

from ..local import local_tool
from ..types import ToolContext

STOPWORDS = frozenset("""
حي حى district dist مدينه city منطقه area في عند قريب من جنب near in at the انا ساكن اسكن ابي
""".split())

RRF_K = 10
TOP_N = 20
FUZZY_MATCH = 85        # confident name match
VECTOR_MATCH = 0.85     # confident semantic match (cosine)
CITY_MATCH = 90         # query is (almost exactly) a city name
EMBED_TIMEOUT_S = 1.5   # voice latency budget; fall back to name matching beyond this
EMBED_RETRY_AFTER_S = 60
_embed_down_until = 0.0

log = logging.getLogger(__name__)

Embedder = Callable[[str], Awaitable[list[float]]]
_embedder: Embedder | None = None


def set_embedder(fn: Embedder | None) -> None:
    """Override the embedding function (tests / offline evals)."""
    global _embedder, _embed_down_until
    _embedder, _embed_down_until = fn, 0.0


async def _embed(text: str) -> list[float]:
    global _embedder
    if _embedder is None:
        from runtime.providers import create
        provider = create("embedding", "custom_http")

        async def call(t: str) -> list[float]:
            return (await provider.embed([t]))[0]
        _embedder = call
    return await _embedder(text)


@lru_cache(maxsize=4)
def _name_index(ref_id: int, locations: tuple[Location, ...]):
    districts, cities = [], {}
    for i, loc in enumerate(locations):
        city_keys = [core_tokens(c, STOPWORDS) for c in (loc.city_ar, loc.city_en) if c]
        dist_keys = [core_tokens(d, STOPWORDS) for d in (loc.district_ar, loc.district_en) if d]
        keys = dist_keys + [f"{d} {c}" for d in dist_keys for c in city_keys]
        districts.append(tuple(k for k in keys if k))
        for c in city_keys:
            cities.setdefault(c, []).append(i)
    return tuple(districts), cities


def _place(loc: Location, lat: float | None = None, lng: float | None = None) -> dict:
    return {"district_ar": loc.district_ar, "district_en": loc.district_en, "city_ar": loc.city_ar,
            "city_en": loc.city_en, "latitude": lat if lat is not None else loc.latitude,
            "longitude": lng if lng is not None else loc.longitude}


async def search_locations(ref: ReferenceData, query: str) -> dict:
    q = core_tokens(query, STOPWORDS)
    if len(q) < 2:
        return {"status": "none", "reason": "no place name in the query"}
    locs = ref.locations
    district_keys, city_index = _name_index(id(ref), tuple(locs))

    # Fuzzy name scores
    fuzzy = np.array([max((max(fuzz.ratio(q, k), fuzz.token_set_ratio(q, k) * 0.95) for k in keys), default=0)
                      for keys in district_keys])

    # City-only query → centroid of that city's districts. A city name wins over a same-named district
    # ("الرياض" is also a district in Jeddah) unless the caller explicitly said district ("حي").
    city_key, city_score = max(((c, fuzz.ratio(q, c)) for c in city_index), key=lambda cs: cs[1])
    said_district = any(w in normalize(query).split() for w in ("حي", "حى", "district", "dist"))
    if city_score >= CITY_MATCH and not said_district:
        idx = city_index[city_key]
        c = locs[idx[0]]
        lat = float(np.mean([locs[i].latitude for i in idx]))
        lng = float(np.mean([locs[i].longitude for i in idx]))
        return {"status": "city", "location": {"city_ar": c.city_ar, "city_en": c.city_en,
                                               "latitude": round(lat, 6), "longitude": round(lng, 6)}}

    # Semantic scores (in-memory cosine; stored rows are L2-normalized). If the embedding service is
    # slow or unreachable, degrade to name matching instead of failing the caller's request.
    global _embed_down_until
    semantic = np.zeros(len(locs), dtype=np.float32)
    if time.monotonic() >= _embed_down_until:      # circuit breaker: don't pay the timeout on every call
        try:
            vec = np.asarray(await asyncio.wait_for(_embed(query), EMBED_TIMEOUT_S), dtype=np.float32)
            vec /= np.linalg.norm(vec)
            semantic = ref.embeddings @ vec
        except Exception as e:  # timeout / connection error
            _embed_down_until = time.monotonic() + EMBED_RETRY_AFTER_S
            log.warning("embedding unavailable for %ss, name-only location search: %r", EMBED_RETRY_AFTER_S, e)

    # Reciprocal-rank fusion of the two rankings
    rrf = np.zeros(len(locs))
    for scores in ((fuzzy, semantic) if semantic.any() else (fuzzy,)):
        order = np.argsort(-scores)[:TOP_N]
        rrf[order] += 1.0 / (RRF_K + np.arange(1, len(order) + 1))
    ranked = np.argsort(-rrf)[:3]
    best = int(ranked[0])

    confident = fuzzy[best] >= FUZZY_MATCH or semantic[best] >= VECTOR_MATCH
    evidence = {"name_score": round(float(fuzzy[best])), "semantic_score": round(float(semantic[best]), 3)}
    if confident:
        return {"status": "match", "location": _place(locs[best]), **evidence}
    return {"status": "ambiguous", "candidates": [_place(locs[int(i)]) for i in ranked], **evidence}


class ResolveLocationArgs(BaseModel):
    query: str = Field(description="Only the place the caller said (district and/or city), "
                                   "e.g. 'حي النرجس' or 'الملقا بالرياض' or 'Al Olaya'")


@local_tool(
    "resolve_location",
    "Resolve the caller's current location (district / city) to coordinates — use it whenever the caller says "
    "where they are (\"I'm at / near / in …\", \"أنا في / ساكن / قريب من …\"), even if the place shares its name "
    "with a hospital. Returns status 'match' "
    "or 'city' with latitude/longitude (pass them to mssql_get_Projects_from_Location), 'ambiguous' with "
    "up to 3 candidate places to confirm with the caller, or 'none'.",
    ResolveLocationArgs,
)
async def resolve_location(args: ResolveLocationArgs, ctx: ToolContext) -> dict:
    return await search_locations(await get_reference(), args.query)
