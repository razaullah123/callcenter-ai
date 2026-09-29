import numpy as np
import pytest

from runtime.data.reference import Location, Project, ReferenceData
from runtime.text.arabic import normalize
from runtime.tools.local_tools import locations as loc_mod
from runtime.tools.local_tools.hospitals import search_hospitals
from runtime.tools.local_tools.locations import search_locations


def _project(ref_id, name, city_en, city_ar, aliases):
    return Project(ref_id, name, f"HMG {name}", city_en, city_ar, 24.7, 46.6, tuple(aliases))


REF = ReferenceData(
    projects=[
        _project(377, "AlHamra Hospital", "Riyadh", "الرياض",
                 ["Al Hamra", "HMR", "Riyadh", "الرياض", "مستشفى الحمراء", "حي الحمراء"]),
        _project(214, "AlKharj Hospital", "Riyadh", "الرياض", ["Al Kharj", "KRJ", "Riyadh", "الرياض", "مستشفى الخرج"]),
        _project(12, "Olaya Hospital", "Riyadh", "الرياض", ["Olaya", "Riyadh", "الرياض", "مستشفى العليا"]),
    ],
    locations=[
        Location("Riyadh", "الرياض", "Al Malqa Dist.", "حي الملقا", 24.80, 46.60),
        Location("Riyadh", "الرياض", "Al Olaya Dist.", "حي العليا", 24.69, 46.68),
        Location("Jeddah", "جدة", "Ar Rawdah Dist.", "حي الروضة", 21.57, 39.16),
        Location("Jeddah", "جدة", "Ar Riyadh Dist.", "حي الرياض", 21.86, 39.18),
    ],
    embeddings=np.eye(4, dtype=np.float32),
)


@pytest.fixture(autouse=True)
def fake_embedder():
    async def embed(text: str):
        # Nearest one-hot to whichever district name appears in the text; else uniform.
        for i, loc in enumerate(REF.locations):
            if normalize(loc.district_ar.replace("حي ", "")) in normalize(text):
                v = [0.1] * 4
                v[i] = 1.0
                return v
        return [0.5] * 4
    loc_mod.set_embedder(embed)
    yield
    loc_mod.set_embedder(None)


@pytest.mark.parametrize("query,ref_id", [
    ("الحمراء", 377), ("مستشفى الحمرا", 377), ("الحَمْرَاء", 377), ("Al-Hamra hospital", 377),
    ("الخرج", 214), ("العليا", 12), ("olaya", 12),
])
def test_hospital_match(query, ref_id):
    r = search_hospitals(REF, query)
    assert r["status"] == "match" and r["hospital"]["project_id"] == ref_id
    assert r["hospital"]["name_ar"].startswith("مستشفى")


@pytest.mark.parametrize("query", ["مستشفى", "hospital", "الرياض", "KRJ", "بيتزا"])
def test_hospital_generic_city_or_unknown_is_not_a_match(query):
    assert search_hospitals(REF, query)["status"] in ("none", "ambiguous")


async def test_location_district_match():
    r = await search_locations(REF, "أنا ساكن في الملقا")
    assert r["status"] == "match" and r["location"]["district_ar"] == "حي الملقا"
    assert (r["location"]["latitude"], r["location"]["longitude"]) == (24.80, 46.60)


async def test_location_city_beats_same_named_district():
    r = await search_locations(REF, "الرياض")
    assert r["status"] == "city" and r["location"]["city_ar"] == "الرياض"
    assert r["location"]["latitude"] == pytest.approx((24.80 + 24.69) / 2)


async def test_location_explicit_district_wins():
    r = await search_locations(REF, "حي الرياض في جدة")
    assert r["status"] == "match" and r["location"]["district_ar"] == "حي الرياض"
    assert r["location"]["city_ar"] == "جدة"


async def test_location_disambiguates_by_city():
    r = await search_locations(REF, "الروضه في جده")
    assert r["status"] == "match" and r["location"]["city_en"] == "Jeddah"


async def test_location_unknown_is_ambiguous_or_none():
    r = await search_locations(REF, "قرب البحر الميت")
    assert r["status"] in ("ambiguous", "none")
