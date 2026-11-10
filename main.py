"""Entry point: run the local dashboard and agent backend.

    python main.py

Then open http://127.0.0.1:8765 in a browser. The automation browser is a
separate visible Chrome window that opens when you connect Moodle.
"""

from __future__ import annotations

import logging

import uvicorn

from app.config import load_settings
from app.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level, settings.logs_dir)
    logger.info("Starting MoodleAI on http://%s:%s", settings.app_host, settings.app_port)
    logger.info("Model: %s via %s", settings.ollama_model, settings.ollama_host)
    uvicorn.run(
        "app.api:app",
        host=settings.app_host,
        port=settings.app_port,
        log_level=settings.log_level.lower(),
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
