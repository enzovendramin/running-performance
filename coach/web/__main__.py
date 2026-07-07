"""Run the local dashboard: `python -m coach.web` (Ctrl+C to stop).

Binds to localhost only — this is a personal, single-user app with no auth.
It reads the local coach.db and sends nothing over the network.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("COACH_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("COACH_WEB_PORT", "8000"))
    print(f"Coach dashboard on http://{host}:{port}")
    uvicorn.run("coach.web.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
