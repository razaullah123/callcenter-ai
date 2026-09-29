"""Event sinks: console (human-readable) and JSONL files (machine-readable, per day).

The Redis Streams sink that feeds the live console is added in Phase 9.
"""

import json
import sys
from pathlib import Path
from typing import TextIO

from .schema import LEVEL_ORDER, Event, Level

_COLORS = {Level.DEBUG: "\033[90m", Level.INFO: "", Level.WARNING: "\033[33m", Level.ERROR: "\033[31m"}
_RESET = "\033[0m"


class ConsoleSink:
    def __init__(self, min_level: Level = Level.INFO, stream: TextIO = sys.stdout, color: bool = True) -> None:
        self.min_level = LEVEL_ORDER[min_level]
        self.stream = stream
        self.color = color and stream.isatty()

    async def __call__(self, e: Event) -> None:
        if LEVEL_ORDER[e.level] < self.min_level:
            return
        where = "/".join(p for p in (e.skill, e.step) if p)
        parts = [
            e.ts.strftime("%H:%M:%S.%f")[:-3],
            (e.call_id or "-")[:10],
            f"t{e.turn_id}" if e.turn_id is not None else "",
            f"{e.type.value:<16}",
            f"[{where}]" if where else "",
            f"{e.latency_ms:.0f}ms" if e.latency_ms is not None else "",
            json.dumps(e.data, ensure_ascii=False, default=str) if e.data else "",
        ]
        line = " ".join(p for p in parts if p)
        if self.color and _COLORS[e.level]:
            line = f"{_COLORS[e.level]}{line}{_RESET}"
        print(line, file=self.stream, flush=True)


class JsonlSink:
    """Appends one JSON event per line to `<dir>/events-YYYY-MM-DD.jsonl`."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._day: str | None = None
        self._fh: TextIO | None = None

    def _file_for(self, e: Event) -> TextIO:
        day = e.ts.strftime("%Y-%m-%d")
        if day != self._day or self._fh is None:
            if self._fh:
                self._fh.close()
            self._fh = open(self.directory / f"events-{day}.jsonl", "a", encoding="utf-8")
            self._day = day
        return self._fh

    async def __call__(self, e: Event) -> None:
        fh = self._file_for(e)
        fh.write(e.model_dump_json() + "\n")
        fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None
