"""find_hospital_by_name — match the hospital name a caller said against `projects.aliases`."""

import re
from dataclasses import dataclass
from functools import lru_cache

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from runtime.data.reference import Project, ReferenceData, get_reference
from runtime.text.arabic import core_tokens, normalize

from ..local import local_tool
from ..types import ToolContext

# Generic words that don't identify a specific hospital (normalized form, article stripped).
STOPWORDS = frozenset("""
مستشفي مستشفيات مستوصف مركز مراكز طبي طبيه تخصصي تخصصيه عام عامه صحي صحيه حي مجموعه دكتور د سليمان
حبيب عيادات عياده فرع في ابي ابغي
hmg hospital hospitals medical center centre clinic clinics dr doctor sulaiman suliman sulaiman habib
group specialist specialized general branch the
""".split())

_ARABIC = re.compile(r"[؀-ۿ]")

MATCH_SCORE = 88        # ≥ this and clearly ahead of the runner-up → single match
MATCH_MARGIN = 8
CANDIDATE_SCORE = 70    # ≥ this → offered as a candidate


@dataclass(frozen=True)
class _Indexed:
    project: Project
    keys: tuple[str, ...]   # normalized alias cores
    name_ar: str | None


@lru_cache(maxsize=4)
def _index(ref_id: int, projects: tuple[Project, ...]) -> tuple[_Indexed, ...]:
    out = []
    for p in projects:
        cities = {core_tokens(c, STOPWORDS) for c in (p.city_en, p.city_ar) if c}
        keys = []
        for alias in (p.project_name, p.ar_name, *p.aliases):
            key = core_tokens(alias or "", STOPWORDS)
            # Skip city names ("Riyadh" is an alias of every Riyadh hospital) and short codes ("KRJ").
            if len(key) < 3 or key in cities or (alias and alias.isupper() and len(alias) <= 4):
                continue
            keys.append(key)
        name_ar = spoken_arabic_name(p)
        out.append(_Indexed(p, tuple(dict.fromkeys(keys)), name_ar))
    return tuple(out)


def spoken_arabic_name(p: Project) -> str | None:
    """Best Arabic name to say aloud: "مستشفى العليا" rather than "مركز مستشفى العليا"."""
    arabic = [a for a in p.aliases if _ARABIC.search(a) and normalize(a) != normalize(p.city_ar or "")]
    return next((a for prefix in ("مستشفى ", "مركز ") for a in arabic
                 if a.startswith(prefix) and not a.startswith(("مستشفى مستشفى", "مستشفى مركز", "مركز مركز",
                                                               "مركز مستشفى"))), None)


def _score(query: str, key: str) -> float:
    if query == key:
        return 100.0
    return max(fuzz.ratio(query, key), fuzz.token_set_ratio(query, key) * 0.97)


def _as_result(ix: _Indexed, score: float) -> dict:
    p = ix.project
    return {"project_id": p.reference_id, "name_en": p.project_name, "name_ar": ix.name_ar,
            "city_en": p.city_en, "city_ar": p.city_ar, "score": round(score)}


def search_hospitals(ref: ReferenceData, query: str) -> dict:
    q = core_tokens(query, STOPWORDS)
    if len(q) < 2:
        return {"status": "none", "reason": "no hospital name in the query"}
    scored = []
    for ix in _index(id(ref), tuple(ref.projects)):
        best = max((_score(q, k) for k in ix.keys), default=0.0)
        scored.append((best, ix))
    scored.sort(key=lambda s: -s[0])
    top_score, top = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if top_score >= MATCH_SCORE and top_score - runner_up >= MATCH_MARGIN:
        return {"status": "match", "hospital": _as_result(top, top_score)}
    candidates = [_as_result(ix, s) for s, ix in scored[:3] if s >= CANDIDATE_SCORE]
    if candidates:
        return {"status": "ambiguous", "candidates": candidates}
    return {"status": "none"}


class FindHospitalArgs(BaseModel):
    query: str = Field(description="Only the hospital / medical center name as the caller said it, "
                                   "e.g. 'الحمراء' or 'مستشفى الخرج' or 'Al Nakheel'")


@local_tool(
    "find_hospital_by_name",
    "Find a Dr. Sulaiman Al Habib hospital or medical center by the name the caller said — only when they name "
    "the hospital they want. If they say where they ARE (\"I'm at / near / in X\", \"أنا في / ساكن / قريب من X\"), "
    "use resolve_location instead, even if X is also a hospital name (Olaya, Al Hamra, Al Rayyan …). "
    "Returns status 'match' with the hospital (use its project_id for clinics and booking), "
    "'ambiguous' with up to 3 candidates to confirm with the caller, or 'none'.",
    FindHospitalArgs,
)
async def find_hospital_by_name(args: FindHospitalArgs, ctx: ToolContext) -> dict:
    return search_hospitals(await get_reference(), args.query)
