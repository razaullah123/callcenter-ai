"""Verify the embedding service reproduces the vectors stored in `locations.embedding`,
and find which text format was embedded. Run where EMBEDDING_URL is reachable (VPN / internal network).

    python scripts/check_embedding.py [--field text] [--mode single|batch]

Cosine ≈ 1.00 for a format means: same model AND same text format → search will work.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from runtime.config import get_settings  # noqa: E402
from runtime.providers import create  # noqa: E402
from runtime.providers.custom_http import location_embedding_text  # noqa: E402

FORMATS = {
    # Format the stored vectors were built with (verified 2026-09-27, cosine 1.0000):
    "city_en city_ar district_en district_ar": lambda r: location_embedding_text(r),
    "district_ar": lambda r: r["district_name_ar"],
    "district_en": lambda r: r["district_name_en"],
    "district_ar city_ar": lambda r: f"{r['district_name_ar']} {r['city_name_ar']}",
    "district_ar, city_ar": lambda r: f"{r['district_name_ar']}, {r['city_name_ar']}",
    "district_en, city_en": lambda r: f"{r['district_name_en']}, {r['city_name_en']}",
    "ar + en": lambda r: f"{r['district_name_ar']} {r['city_name_ar']} {r['district_name_en']} {r['city_name_en']}",
    "aliases json": lambda r: r["aliases"],
    "aliases json (ensure_ascii=False)": lambda r: json.dumps(json.loads(r["aliases"]), ensure_ascii=False),
    "aliases values": lambda r: " ".join(json.loads(r["aliases"]).values()),
}


def cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


async def main(field: str, mode: str) -> None:
    emb = create("embedding", "custom_http", {"text_field": field, "mode": mode})
    conn = await asyncpg.connect(get_settings().database_url.get_secret_value(), ssl=False)
    rows = await conn.fetch("""SELECT city_name_ar, city_name_en, district_name_ar, district_name_en,
                                      aliases::text AS aliases, embedding::text AS embedding
                               FROM locations WHERE embedding IS NOT NULL ORDER BY random() LIMIT 3""")
    await conn.close()

    best = {}
    for r in rows:
        stored = json.loads(r["embedding"])
        print(f"\n{r['district_name_ar']} / {r['district_name_en']} ({r['city_name_en']})")
        for name, fmt in FORMATS.items():
            text = fmt(r)
            [vec] = await emb.embed([text])
            c = cosine(vec, stored)
            best.setdefault(name, []).append(c)
            print(f"  {c:6.3f}  {name}")

    name, scores = max(best.items(), key=lambda kv: np.mean(kv[1]))
    print(f"\nBest format: {name!r}  mean cosine {np.mean(scores):.3f}")
    print("→ OK: model and format match" if np.mean(scores) > 0.999 else
          "→ No exact match: model or text format differs — share how the embeddings were generated")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="text", help="JSON field the service expects (text / input / …)")
    ap.add_argument("--mode", default="single", choices=["single", "batch"])
    a = ap.parse_args()
    asyncio.run(main(a.field, a.mode))
