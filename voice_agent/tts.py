"""ElevenLabs TTS WebSocket client.

Streams text into ElevenLabs' text-to-speech WebSocket and receives
audio chunks back. Uses the stream-input endpoint.

Text arrives pre-buffered to sentence boundaries and sanitized by
voice_agent/speech_filter.py. This client just forwards chunks to
ElevenLabs and streams audio back.

Audio is returned as raw ulaw 8kHz, which goes directly to Twilio
with no transcoding needed.
"""

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable

import websockets

from config import ELEVENLABS_API_KEY, ELEVENLABS_MODEL, ELEVENLABS_VOICE_ID

logger = logging.getLogger(__name__)

OUTPUT_FORMAT = "ulaw_8000"


class TTSClient:
    """Streaming TTS client using ElevenLabs' WebSocket API."""

    def __init__(
        self,
        on_audio: Callable[[bytes, dict | None], Awaitable[None]],
        on_flushed: Callable[[], Awaitable[None]],
    ):
        self.on_audio = on_audio
        self.on_flushed = on_flushed
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._receive_task: asyncio.Task | None = None
        self._connected = False

    async def connect(self):
        """Open the TTS WebSocket connection."""
        url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
            f"/stream-input"
            f"?model_id={ELEVENLABS_MODEL}"
            f"&output_format={OUTPUT_FORMAT}"
        )

        logger.debug(f"[TTS] Connecting to ElevenLabs (model={ELEVENLABS_MODEL})")

        self._ws = await websockets.connect(
            url,
            additional_headers={"xi-api-key": ELEVENLABS_API_KEY},
            max_size=16 * 1024 * 1024,
        )
        self._connected = True

        # Init message — minimal, per ElevenLabs docs
        await self._ws.send(json.dumps({
            "text": " ",
            "alignment": True,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": 1.0,
            },
        }))

        # Start the receive loop
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.debug("[TTS] Connected")

    async def _receive_loop(self):
        """Receive audio chunks from ElevenLabs."""
        try:
            async for message in self._ws:
                data = json.loads(message)

                # Log every message type from ElevenLabs
                keys = list(data.keys())
                has_audio = bool(data.get("audio"))
                logger.debug(f"[TTS] Received message keys={keys} has_audio={has_audio} isFinal={data.get('isFinal')}")

                if data.get("audio"):
                    audio_bytes = base64.b64decode(data["audio"])
                    if len(audio_bytes) > 0:
                        alignment = data.get("normalizedAlignment")
                        logger.debug(
                            f"[TTS] Audio chunk: {len(audio_bytes)} bytes, "
                            f"alignment={'yes' if alignment else 'no'}"
                        )
                        await self.on_audio(audio_bytes, alignment)

                if data.get("isFinal"):
                    logger.debug("[TTS] Got isFinal — ending receive loop")
                    break

        except websockets.exceptions.ConnectionClosed as e:
            logger.debug(f"[TTS] WebSocket closed: {e}")
        except Exception as e:
            logger.error(f"[TTS] Receive error: {e}")
        finally:
            self._connected = False
            await self.on_flushed()

    async def send_text(self, text: str):
        """Send a text chunk to ElevenLabs for synthesis.

        SentenceBuffer + sanitize_for_speech (speech_filter.py) handle
        buffering to sentence boundaries and cleaning up formatting.
        By the time text reaches here, it's a complete, clean sentence.
        """
        if not self._ws or not self._connected:
            return

        logger.debug(f"[TTS] Sending chunk to ElevenLabs: {text!r}")
        try:
            await self._ws.send(json.dumps({"text": text}))
        except Exception as e:
            logger.debug(f"[TTS] Send failed: {e}")

    async def flush(self):
        """Signal end of input — flush ElevenLabs buffer and close stream."""
        if not self._ws or not self._connected:
            return

        try:
            logger.debug("[TTS] Sending flush command")
            await self._ws.send(json.dumps({"flush": True}))

            logger.debug("[TTS] Sending end-of-stream")
            await self._ws.send(json.dumps({"text": ""}))
        except Exception as e:
            logger.debug(f"[TTS] Flush failed: {e}")

    async def clear(self):
        """Cancel current synthesis (barge-in). Closes the connection."""
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def reconnect(self):
        """Reconnect for a new utterance."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        await self.connect()

    async def close(self):
        """Close the TTS connection."""
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.debug("[TTS] Connection closed")
