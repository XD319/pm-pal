import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from pm_pal.utils.logging import build_formatter, setup_logging

load_dotenv()

# Create logs directory if it doesn't exist
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

setup_logging()

file_handler = logging.FileHandler(logs_dir / "app.log", encoding="utf-8")
file_handler.setLevel(
    getattr(logging, os.getenv("LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
)
file_handler.setFormatter(build_formatter(os.getenv("LOG_FORMAT", "human")))
logging.getLogger().addHandler(file_handler)

# Create logger instance
logger = logging.getLogger(__name__)

from pm_pal.server.app import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    # Desktop client binds loopback by default; shared/container deploys override host. :-)
    host = os.getenv("PM_PAL_HOST", os.getenv("MARRDP_HOST", "127.0.0.1")).strip() or "127.0.0.1"
    port = int(os.getenv("PM_PAL_PORT", os.getenv("MARRDP_PORT", "8000")).strip() or "8000")
    logger.info("Starting server on %s:%s ...", host, port)
    uvicorn.run(app, host=host, port=port)
