"""Launch the InsureAI Orchestrator API locally.

Usage:
    py scripts/run_api.py                # default host:port from .env
    py scripts/run_api.py --reload       # hot-reload for development

Reads API_HOST / API_PORT from .env.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "ai-orchestrator"))


def main() -> None:
    import uvicorn  # noqa: E402

    from config import get_settings  # type: ignore[import-not-found]  # noqa: E402

    parser = argparse.ArgumentParser(description="Run the InsureAI Orchestrator API")
    parser.add_argument("--reload", action="store_true", help="Enable hot-reload (dev only)")
    parser.add_argument("--host",   default=None,        help="Override API_HOST")
    parser.add_argument("--port",   type=int, default=None, help="Override API_PORT")
    args = parser.parse_args()

    settings = get_settings()
    host = args.host or settings.api_host
    port = args.port or settings.api_port

    print(f"[run_api] Starting orchestrator on http://{host}:{port}")
    print(f"[run_api] OpenAPI docs at http://{host}:{port}/docs")

    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=args.reload,
        app_dir=str(_ROOT / "ai-orchestrator"),
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
