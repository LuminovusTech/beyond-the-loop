"""Telephony routes — Twilio webhook + WebSocket handler.

Two endpoints:
  POST /incoming-call   — Returns TwiML to open a media stream
  WS   /twilio          — Receives Twilio audio stream, creates a VoiceAgentSession
"""

import json
import logging

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from config import SERVER_EXTERNAL_URL, WEBHOOK_SECRET
from voice_agent.session import VoiceAgentSession
from voice_agent.logging_setup import get_debug_events_logger

logger = logging.getLogger(__name__)
debug_events = get_debug_events_logger()

# Active sessions keyed by call_sid (for monitoring/cleanup)
active_sessions: dict[str, VoiceAgentSession] = {}


def _check_webhook_secret(path_params: dict) -> bool:
    if not WEBHOOK_SECRET:
        return True
    return path_params.get("token", "") == WEBHOOK_SECRET


async def incoming_call(request: Request) -> Response:
    """Twilio calls this when someone dials in.

    Returns TwiML that tells Twilio to open a bidirectional WebSocket
    audio stream back to our /twilio endpoint.
    """
    if not _check_webhook_secret(request.path_params):
        return Response(status_code=404)

    if SERVER_EXTERNAL_URL:
        host = SERVER_EXTERNAL_URL.replace("https://", "").replace("http://", "").rstrip("/")
    else:
        host = request.headers.get("host", "localhost:8080")

    ws_path = "/twilio"
    if WEBHOOK_SECRET:
        ws_path = f"/twilio/{WEBHOOK_SECRET}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}{ws_path}" />
    </Connect>
</Response>"""

    debug_events.info(f"[TELEPHONY] Incoming call — streaming to wss://{host}{ws_path}")
    return Response(content=twiml, media_type="application/xml")


async def twilio_websocket(websocket: WebSocket):
    """Handle a Twilio audio stream WebSocket connection.

    Protocol:
      1. Twilio sends "connected" event
      2. Twilio sends "start" event with callSid/streamSid
      3. Twilio sends "media" events with base64 mulaw audio
      4. We send "media" events back with agent audio
      5. Twilio sends "stop" when call ends
    """
    if not _check_webhook_secret(websocket.path_params):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    debug_events.info("[TELEPHONY] WebSocket connected")

    call_sid = None
    stream_sid = None
    session = None

    try:
        # Wait for the "start" event to get call metadata
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data.get("event") == "start":
                call_sid = data["start"].get("callSid", "unknown")
                stream_sid = data["start"].get("streamSid", "unknown")
                debug_events.info(f"[TELEPHONY] Call started — callSid={call_sid}")
                break
            elif data.get("event") == "connected":
                continue

        # Create and run the voice agent session
        session = VoiceAgentSession(websocket, call_sid, stream_sid)
        active_sessions[call_sid] = session

        await session.start()
        await session.run()

    except Exception as e:
        logger.error(f"[TELEPHONY] Error in call {call_sid}: {e}")
    finally:
        if session:
            await session.cleanup()
        if call_sid and call_sid in active_sessions:
            del active_sessions[call_sid]
        debug_events.info(f"[TELEPHONY] Call {call_sid} ended")


# Routes — exported for use by app.py
telephony_routes = [
    Route("/incoming-call/{token:path}", incoming_call, methods=["POST"]),
    Route("/incoming-call", incoming_call, methods=["POST"]),
    WebSocketRoute("/twilio/{token:path}", twilio_websocket),
    WebSocketRoute("/twilio", twilio_websocket),
]
