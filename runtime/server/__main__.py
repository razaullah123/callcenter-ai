import logging
import sys

import uvicorn

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from runtime.config import get_settings
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    workers = get_settings().workers
    # Each worker is an independent process (~50 concurrent calls each). The console's live view shows the
    # calls of the worker it is connected to; history / stats come from the shared database.
    uvicorn.run("runtime.server.app:app", host="0.0.0.0", port=port, log_level="info", ws_ping_interval=20,
                workers=workers if workers > 1 else None)
