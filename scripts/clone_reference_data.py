"""Create the voice agent database and copy `projects` + `locations` from the source database.

    python scripts/clone_reference_data.py --source postgresql://user:pass@127.0.0.1:5432/ai_agent_patient_appointment

Target = DATABASE_URL from .env (created if missing). Schema = db/schema.sql.
Re-running refreshes the data (upsert by id); rows are never deleted.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.config import get_settings  # noqa: E402

TABLES = {
    "projects": ["id", "project_name", "ar_name", "reference_id", "latitude", "longitude", "aliases", "timezone",
                 "active", "created_at", "updated_at", "city_name_en", "city_name_ar", "base_extension", "prefix_allow"],
    "locations": ["id", "city_name_en", "city_name_ar", "district_name_en", "district_name_ar", "latitude",
                  "longitude", "aliases", "active", "created_at", "updated_at", "embedding"],
}
CASTS = {"aliases": "jsonb", "embedding": "vector"}


async def ensure_database(target: str) -> None:
    u = urlparse(target)
    name = u.path.lstrip("/")
    admin = await asyncpg.connect(urlunparse(u._replace(path="/postgres")), ssl=False, timeout=10)
    try:
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            await admin.execute(f'CREATE DATABASE "{name}"')
            print(f"created database {name}")
        else:
            print(f"database {name} exists")
    finally:
        await admin.close()


async def copy_table(src: asyncpg.Connection, dst: asyncpg.Connection, table: str, cols: list[str]) -> int:
    # jsonb / vector travel as text and are cast back on insert (no custom codecs needed).
    select = ", ".join(f"{c}::text" if c in CASTS else c for c in cols)
    rows = await src.fetch(f"SELECT {select} FROM {table}")
    placeholders = ", ".join(f"${i}::{CASTS[c]}" if c in CASTS else f"${i}" for i, c in enumerate(cols, 1))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id")
    await dst.executemany(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT (id) DO UPDATE SET {updates}",
        [tuple(r) for r in rows])
    return len(rows)


async def main(source: str) -> None:
    target = get_settings().database_url.get_secret_value()
    if urlparse(source).path == urlparse(target).path:
        raise SystemExit("source and target databases are the same — set DATABASE_URL to the new database")
    await ensure_database(target)

    src = await asyncpg.connect(source, ssl=False, timeout=10)
    dst = await asyncpg.connect(target, ssl=False, timeout=10)
    try:
        await dst.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
        async with dst.transaction():
            for table, cols in TABLES.items():
                n = await copy_table(src, dst, table, cols)
                print(f"{table}: {n} rows copied")
        for table in TABLES:
            print(f"{table}: {await dst.fetchval(f'SELECT count(*) FROM {table}')} rows in target")
        print("embeddings:", await dst.fetchval("SELECT count(*) FROM locations WHERE embedding IS NOT NULL"),
              "dims:", await dst.fetchval("SELECT vector_dims(embedding) FROM locations WHERE embedding IS NOT NULL LIMIT 1"))
    finally:
        await src.close()
        await dst.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="source database URL")
    asyncio.run(main(ap.parse_args().source))
