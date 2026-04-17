"""Centralized logging configuration with three verbosity levels.

Single entry point — configure(verbosity) — sets per-logger levels so
we don't have to sprinkle verbosity checks across the codebase.

Verbosity levels:
  0 (default)  — quiet TUI mode. Init lines + tool calls + warnings/errors.
                 The TranscriptView handles turn-by-turn display separately.
  1 (-v)       — TUI + extra iteration signals (barge-in, commit details,
                 resumption, TurnResumed). Good for iterating on
                 interruption behavior during development.
  2 (-vv)      — raw firehose. Every Flux Update, every TTS message,
                 per-turn WS reconnects, httpx requests, etc. At this
                 level the TUI is disabled — you want plain logs.

Adding a new log line? Pick a logger and level that matches the tier
you want it visible at. If it's "interesting when I'm iterating but
noise for a demo", use logger.getLogger("voice_agent.debug_events")
at INFO — it's off at default, on at -v and above.
"""

import logging
import os


# Logger dedicated to "dev iteration" signals: barge-in, commit, resumption.
# Hidden at default verbosity, shown at -v and above.
DEBUG_EVENTS_LOGGER = "voice_agent.debug_events"


def get_verbosity_from_env() -> int:
    """Read LOG_VERBOSITY env var, clamp to 0..2."""
    try:
        v = int(os.environ.get("LOG_VERBOSITY", "0"))
    except ValueError:
        v = 0
    return max(0, min(2, v))


def tui_enabled() -> bool:
    """Whether the Rich TUI should be active.

    TUI is on at default and -v. At -vv we want raw logs only —
    Rich Live and streaming log output fight each other.
    """
    return get_verbosity_from_env() < 2


def configure(verbosity: int) -> None:
    """Configure logging for the given verbosity level.

    Called once at startup from app.py. Sets the root formatter and
    per-logger levels. Idempotent — safe to call again if verbosity
    changes.
    """
    # Root formatter — same format the codebase already uses.
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        root.addHandler(handler)

    if verbosity >= 2:
        # Firehose. Everything at DEBUG, nothing filtered.
        root.setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)
        logging.getLogger(DEBUG_EVENTS_LOGGER).setLevel(logging.DEBUG)
        return

    # verbosity 0 or 1: quiet the chatty third-party loggers.
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    # Iteration signals (barge-in, commit, resumption) are on the
    # debug_events logger.  Off at default, on at -v.
    if verbosity >= 1:
        logging.getLogger(DEBUG_EVENTS_LOGGER).setLevel(logging.INFO)
    else:
        logging.getLogger(DEBUG_EVENTS_LOGGER).setLevel(logging.WARNING)


def get_debug_events_logger() -> logging.Logger:
    """Return the shared logger for dev-iteration events.

    Use this for messages that are useful while developing but noise
    for a clean demo: barge-in details, commit diagnostics, resumption
    context injection, etc.
    """
    return logging.getLogger(DEBUG_EVENTS_LOGGER)
