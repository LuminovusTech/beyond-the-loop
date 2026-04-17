"""Starlette server entry point for the telephony voice agent."""

import logging
import pathlib

import uvicorn
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from config import SERVER_HOST, SERVER_PORT
from voice_agent.logging_setup import configure as configure_logging, get_verbosity_from_env
from voice_agent import tui as tui_module

# Configure logging + TUI first, before anything else logs.
_verbosity = get_verbosity_from_env()
configure_logging(_verbosity)
tui_module.install()

from telephony.routes import telephony_routes

logger = logging.getLogger(__name__)

# Filter out zrok health-check noise (404s on /api/t/, /web-bundler/, /q/health/)
_ZROK_NOISE = ("/api/t/", "/web-bundler/", "/q/health/")

class _ZrokNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in _ZROK_NOISE)

logging.getLogger("uvicorn.access").addFilter(_ZrokNoiseFilter())


MOCK_PHONE_DIR = pathlib.Path(__file__).parent / "mock_phone"


async def health(request):
    return PlainTextResponse("ok")


async def mock_phone_redirect(request):
    return RedirectResponse(url="/mock-phone/index.html")


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/mock-phone", mock_phone_redirect),
        Mount("/mock-phone", StaticFiles(directory=MOCK_PHONE_DIR), name="mock_phone"),
        *telephony_routes,
    ],
)


if __name__ == "__main__":
    logger.info(f"Starting server on {SERVER_HOST}:{SERVER_PORT}")
    uvicorn.run(
        "app:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="info",
    )
