"""Live check of the MCP pool with NON-patient booking tools; saves response shapes for summarizers.

    python scripts/probe_mcp.py [--project 12] [--lat 24.69 --lng 46.68]

Writes logs/probe/<tool>.json (full responses) and prints latency + size per call.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from runtime.config import get_settings  # noqa: E402
from runtime.tools import MCPPool  # noqa: E402

OUT = ROOT / "logs" / "probe"


async def probe(pool: MCPPool, name: str, args: dict) -> object:
    t0 = time.perf_counter()
    ok, data = await pool.call(name, args, timeout_s=20)
    ms = (time.perf_counter() - t0) * 1000
    text = json.dumps(data, ensure_ascii=False, default=str)
    n = len(data) if isinstance(data, list) else (len(data) if isinstance(data, dict) else "-")
    print(f"{'OK ' if ok else 'ERR'} {name:<55} {ms:6.0f} ms  {len(text):7d} chars  items={n}")
    print(f"    {text[:400]}\n")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps({"args": args, "ok": ok, "data": data}, ensure_ascii=False,
                                                 indent=2, default=str), encoding="utf-8")
    return data


def first_id(data, *keys):
    rows = data if isinstance(data, list) else next((v for v in (data or {}).values() if isinstance(v, list)), [])
    for row in rows:
        for k in keys:
            if isinstance(row, dict) and row.get(k) is not None:
                return row[k]
    return None


async def main(project: int, lat: float, lng: float) -> None:
    pool = MCPPool(get_settings().mcp_server_config())
    t0 = time.perf_counter()
    await pool.start()
    print(f"connected in {(time.perf_counter() - t0) * 1000:.0f} ms, {len(pool.schemas())} tools\n")
    try:
        await probe(pool, "mssql_get_Projects_from_Location", {"latitude": lat, "longitude": lng, "LanguageID": 1})
        clinics = await probe(pool, "mssql_get_clinics_for_project", {"projectId": project, "LanguageID": 1})
        clinic = first_id(clinics, "ClinicID", "clinicId", "ClinicId", "ID")
        if clinic is None:
            print("could not find a clinic id in the response; stopping")
            return
        await probe(pool, "mssql_get_TopFive_nearestClinic_have_doctorSlots", {"projectId": project, "clinicId": clinic})
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        await probe(pool, "mssql_get_TopFive_availableDoctors_with_slots_byDate",
                    {"projectId": project, "clinicId": clinic, "date": tomorrow})
        # warm call latency
        t0 = time.perf_counter()
        await pool.call("mssql_get_clinics_for_project", {"projectId": project, "LanguageID": 1}, 20)
        print(f"warm repeat call: {(time.perf_counter() - t0) * 1000:.0f} ms")
    finally:
        await pool.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=int, default=12)   # Olaya Hospital
    ap.add_argument("--lat", type=float, default=24.695)
    ap.add_argument("--lng", type=float, default=46.681)
    a = ap.parse_args()
    asyncio.run(main(a.project, a.lat, a.lng))
