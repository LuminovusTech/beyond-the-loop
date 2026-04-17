"""Flux STT WebSocket client.

Connects to Deepgram's Flux API via websockets and emits TurnInfo events
through a callback. The session layer forwards raw audio to this client
and handles the events it produces.
"""

import json
import logging
from collections.abc import Awaitable, Callable

import websockets

from config import (
    DEEPGRAM_API_KEY,
    EOT_THRESHOLD,
    EOT_TIMEOUT_MS,
    FLUX_ENCODING,
    FLUX_MODEL,
    FLUX_SAMPLE_RATE,
    FLUX_WS_URL,
)
from voice_agent.logging_setup import get_debug_events_logger

logger = logging.getLogger(__name__)
debug_events = get_debug_events_logger()


class FluxSTTClient:
    """WebSocket client for Deepgram Flux (STT with turn-taking)."""

    def __init__(self, on_event: Callable[[dict], Awaitable[None]]):
        """
        Args:
            on_event: Async callback invoked for each TurnInfo event from Flux.
                      The dict contains the parsed JSON message.
        """
        self.on_event = on_event
        self._ws: websockets.WebSocketClientProtocol | None = None

    async def connect(self):
        """Open the WebSocket connection to Flux."""
        url = (
            f"{FLUX_WS_URL}"
            f"?model={FLUX_MODEL}"
            f"&sample_rate={FLUX_SAMPLE_RATE}"
            f"&encoding={FLUX_ENCODING}"
            f"&eot_threshold={EOT_THRESHOLD}"
            f"&eot_timeout_ms={EOT_TIMEOUT_MS}"
        )
        headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

        logger.info(f"[FLUX] Connecting to {FLUX_WS_URL} (model={FLUX_MODEL})")
        self._ws = await websockets.connect(url, additional_headers=headers)
        logger.info("[FLUX] Connected")

    async def send_audio(self, audio_bytes: bytes):
        """Send a chunk of audio to Flux (mulaw 8kHz from Twilio)."""
        if self._ws:
            await self._ws.send(audio_bytes)

    async def receive_loop(self):
        """Read messages from Flux and dispatch events via callback.

        Runs until the WebSocket closes. Should be run as an asyncio task.
        """
        if not self._ws:
            return

        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    message = message.decode("utf-8")

                data = json.loads(message)
                msg_type = data.get("type", "")

                if msg_type == "TurnInfo":
                    event_name = data.get("event", "")
                    transcript = data.get("transcript", "")
                    # Update events with empty transcripts are just keepalives
                    if event_name == "Update" and not transcript:
                        continue
                    # Route by verbosity tier:
                    #   Update   → DEBUG (firehose, -vv only)
                    #   Everything else (turn events) → debug_events
                    #     (off at default because the TUI transcript already
                    #      shows them; on at -v for iteration, raw at -vv)
                    if event_name == "Update":
                        logger.debug(f"[FLUX] {event_name}: {transcript!r}")
                    else:
                        debug_events.info(f"[FLUX] {event_name}: {transcript!r}")
                    await self.on_event(data)
                elif msg_type == "receiveConnected":
                    logger.debug("[FLUX] Receive connected confirmation")
                elif msg_type == "receiveFatalError":
                    logger.error(f"[FLUX] Fatal error: {data.get('error', 'unknown')}")
                    break
                else:
                    logger.debug(f"[FLUX] Unhandled message type: {msg_type}")

        except websockets.exceptions.ConnectionClosed as e:
            debug_events.info(f"[FLUX] Connection closed: {e}")
        except Exception as e:
            logger.error(f"[FLUX] Error in receive loop: {e}")

    async def close(self):
        """Close the WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None
            debug_events.info("[FLUX] Connection closed")
