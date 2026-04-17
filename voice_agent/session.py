"""VoiceAgentSession — orchestrator for a single phone call.

Turn lifecycle:
  - EndOfTurn → generate LLM reply, stream through TTS, send audio to Twilio
  - Mark ACKs from Twilio track what audio the caller has actually heard
  - Last mark ACK → commit full response to conversation history
  - StartOfTurn / SpeechResumed (barge-in) → cancel generation, stop
    playback, trim history to only what was heard, inject resumption context

Two independent state signals drive barge-in:
  - _reply_task: is the LLM/TTS pipeline still generating?
  - _agent_speaking: is Twilio still playing audio to the caller?
  Barge-in checks both — generation can finish before playback does.

Audio flow (no transcoding — everything is mulaw 8kHz):
  Twilio → Flux STT → turn events → LLM → ElevenLabs TTS → Twilio
"""

import asyncio
import base64
import json
import logging

from starlette.websockets import WebSocket

from config import GREETING, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
from voice_agent.stt import FluxSTTClient
from voice_agent.llm import generate as llm_generate
from voice_agent.tts import TTSClient
from voice_agent.tools import TOOLS
from voice_agent.function_handlers import dispatch_function
from voice_agent.playback_tracker import PlaybackTracker
from voice_agent.speech_filter import SentenceBuffer, sanitize_for_speech
from voice_agent.logging_setup import get_debug_events_logger
from voice_agent.tui import get_tui

logger = logging.getLogger(__name__)
debug_events = get_debug_events_logger()


class VoiceAgentSession:
    """Manages one voice agent session for the lifetime of a phone call."""

    def __init__(self, twilio_ws: WebSocket, call_sid: str, stream_sid: str):
        self.twilio_ws = twilio_ws
        self.call_sid = call_sid
        self.stream_sid = stream_sid

        # Conversation history (Responses API input format — no system message)
        self.messages: list = []

        # Service clients
        self.flux_client: FluxSTTClient | None = None
        self.tts_client: TTSClient | None = None

        # Active reply task (one at a time)
        self._reply_task: asyncio.Task | None = None

        # Mark tracking — marks tell us when Twilio has finished playing audio
        self._mark_counter = 0
        self._pending_marks: dict[str, asyncio.Future] = {}

        # Sentence buffering + sanitization before TTS
        self._sentence_buffer = SentenceBuffer()

        # Playback tracking for barge-in trimming
        self.playback_tracker = PlaybackTracker()

        # Agent speaking state — True from first audio sent until last
        # mark ACK received.  Barge-in checks this, not _reply_task.
        self._agent_speaking = False

        # Cleanup
        self._cleanup_done = False
        self._ending_call = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Connect to Flux STT and ElevenLabs TTS."""
        logger.info(f"[SESSION:{self.call_sid}] Starting voice agent session")
        get_tui().call_started(self.call_sid)

        self.flux_client = FluxSTTClient(on_event=self._handle_flux_event)
        await self.flux_client.connect()

        self.tts_client = TTSClient(
            on_audio=self._handle_tts_audio,
            on_flushed=self._handle_tts_flushed,
        )
        await self.tts_client.connect()

        logger.info(f"[SESSION:{self.call_sid}] All services connected")

    async def run(self):
        """Forward audio from Twilio to Flux, process events."""
        flux_receive_task = asyncio.create_task(self.flux_client.receive_loop())
        twilio_forward_task = asyncio.create_task(self._forward_twilio_audio())

        # Agent speaks first so the caller knows they've reached the right
        # place and what the agent can do.
        await self._play_greeting()

        done, pending = await asyncio.wait(
            [flux_receive_task, twilio_forward_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        debug_events.info(f"[SESSION:{self.call_sid}] Run loop ended")

    async def cleanup(self):
        """Release all resources."""
        if self._cleanup_done:
            return
        self._cleanup_done = True
        debug_events.info(f"[SESSION:{self.call_sid}] Cleaning up")

        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
            try:
                await self._reply_task
            except asyncio.CancelledError:
                pass

        if self.flux_client:
            await self.flux_client.close()
        if self.tts_client:
            await self.tts_client.close()

        debug_events.info(f"[SESSION:{self.call_sid}] Cleanup complete")
        get_tui().call_ended(self.call_sid)

    # ------------------------------------------------------------------
    # Twilio audio forwarding
    # ------------------------------------------------------------------

    async def _forward_twilio_audio(self):
        """Read Twilio WebSocket messages and forward audio to Flux."""
        try:
            while True:
                message = await self.twilio_ws.receive_text()
                data = json.loads(message)

                event = data.get("event")

                if event == "media":
                    payload = data["media"]["payload"]
                    audio = base64.b64decode(payload)
                    await self.flux_client.send_audio(audio)

                elif event == "mark":
                    mark_name = data.get("mark", {}).get("name", "")
                    logger.debug(f"[SESSION:{self.call_sid}] Mark ack: {mark_name}")
                    future = self._pending_marks.pop(mark_name, None)
                    if future and not future.done():
                        future.set_result(True)
                    self.playback_tracker.ack_mark(mark_name)

                elif event == "stop":
                    debug_events.info(f"[SESSION:{self.call_sid}] Twilio stream stopped")
                    break

        except Exception as e:
            debug_events.info(f"[SESSION:{self.call_sid}] Twilio WebSocket closed: {e}")

    # ------------------------------------------------------------------
    # Flux event handling
    # ------------------------------------------------------------------

    async def _handle_flux_event(self, data: dict):
        """Process a TurnInfo event from Flux."""
        event = data.get("event", "")

        match event:
            case "StartOfTurn" | "SpeechResumed" | "TurnResumed":
                # User started speaking — cancel generation and/or playback
                was_generating = self._reply_task and not self._reply_task.done()
                was_speaking = self._agent_speaking

                # Show the live user line in the TUI as soon as we know
                # they're speaking.
                transcript = data.get("transcript", "")
                get_tui().user_turn_started(transcript)

                if was_generating:
                    debug_events.info(f"[SESSION:{self.call_sid}] BARGE-IN ({event}): cancelling reply generation")
                    self._reply_task.cancel()
                    try:
                        await self._reply_task
                    except asyncio.CancelledError:
                        pass
                    self._reply_task = None

                if was_speaking:
                    debug_events.info(f"[SESSION:{self.call_sid}] BARGE-IN ({event}): agent was speaking, trimming")
                    self.playback_tracker.mark_interrupted()
                    self._agent_speaking = False
                    self._commit_assistant_response()

                if not was_generating and not was_speaking:
                    debug_events.info(f"[SESSION:{self.call_sid}] {event} — agent was idle")

                if self.tts_client:
                    await self.tts_client.clear()
                await self._send_twilio_clear()
                self._cancel_pending_marks()

            case "EndOfTurn":
                transcript = data.get("transcript", "")
                if not transcript:
                    return
                debug_events.info(f"[SESSION:{self.call_sid}] USER: {transcript}")
                get_tui().user_turn_finalized(transcript)
                self.messages.append({"role": "user", "content": transcript})
                self._reply_task = asyncio.create_task(
                    self._generate_and_play_reply()
                )

            case "Update":
                transcript = data.get("transcript", "")
                if transcript:
                    logger.debug(f"[SESSION:{self.call_sid}] Update: {transcript}")
                    get_tui().user_turn_updated(transcript)

    # ------------------------------------------------------------------
    # TTS callbacks
    # ------------------------------------------------------------------

    async def _handle_tts_audio(self, audio_data: bytes, alignment: dict | None):
        """Called when TTS produces an audio chunk — send to Twilio with a mark."""
        self._agent_speaking = True
        await self._send_audio_to_twilio(audio_data)
        mark_name = await self._send_mark()
        self.playback_tracker.add_segment(
            mark_name=mark_name,
            audio_bytes=audio_data,
            alignment_data=alignment,
        )

    async def _handle_tts_flushed(self):
        """Called when TTS finishes sending all audio — send a turn-end mark."""
        mark_name = await self._send_mark(prefix="turn_end")
        debug_events.info(f"[SESSION:{self.call_sid}] TTS flushed, sent {mark_name}")
        # When the last mark is acked, the agent is done speaking.
        # Wire a callback so _agent_speaking flips and we commit history.
        mark_future = self._pending_marks.get(mark_name)
        if mark_future:
            mark_future.add_done_callback(self._on_playback_complete)

    def _on_playback_complete(self, future: asyncio.Future):
        """Called when the last turn-end mark is ACKed by Twilio.

        This means the caller has heard all the audio.  Commit the full
        response to conversation history (normal completion, no barge-in).
        """
        if future.cancelled():
            return  # barge-in already handled this
        self._agent_speaking = False
        debug_events.info(f"[SESSION:{self.call_sid}] Playback complete — agent done speaking")
        self._commit_assistant_response()

    # ------------------------------------------------------------------
    # Greeting
    # ------------------------------------------------------------------

    async def _play_greeting(self):
        """Speak the opening greeting so the caller knows where they've
        reached and what the agent can do.

        Bypasses the LLM — the greeting is a fixed string we push straight
        into TTS. We still register it with the playback tracker so a
        barge-in during the greeting is handled the same way as any other
        turn, and we seed the conversation history so the LLM has context
        for the caller's first reply.
        """
        if not self.tts_client:
            return

        self.playback_tracker.reset()
        self.playback_tracker.set_full_text(GREETING)
        get_tui().assistant_turn_finalized(GREETING)

        try:
            await self.tts_client.send_text(GREETING)
            await self.tts_client.flush()
        except Exception as e:
            logger.error(f"[SESSION:{self.call_sid}] Greeting TTS error: {e}")
            return

        # History commit happens later via the normal playback path:
        # last turn-end mark ACK → _on_playback_complete → _commit_assistant_response
        debug_events.info(f"[SESSION:{self.call_sid}] GREETING: {GREETING!r}")

    # ------------------------------------------------------------------
    # Reply generation
    # ------------------------------------------------------------------

    async def _generate_and_play_reply(self):
        """Generate an LLM reply and stream it through TTS to Twilio.

        Handles the tool-call loop: if the model emits function_call items,
        execute them, append results, and call the LLM again so it can
        produce a spoken response incorporating the results.
        """
        try:
            self.playback_tracker.reset()
            self._sentence_buffer = SentenceBuffer()
            await self.tts_client.reconnect()

            # First LLM call — may produce text, tool calls, or both
            full_text, output_items = await llm_generate(
                messages=self.messages,
                on_token=self._on_llm_token,
                tools=TOOLS,
            )

            tool_calls = [i for i in output_items if getattr(i, "type", None) == "function_call"]

            if tool_calls:
                # Append the model's output items to conversation history
                # (they round-trip as input for the next turn)
                self.messages.extend(output_items)

                # Execute each function and append results
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.arguments)
                    except json.JSONDecodeError as e:
                        args = {}
                        logger.error(f"[SESSION:{self.call_sid}] Bad tool args: {e}")

                    result = await dispatch_function(tc.name, args)
                    debug_events.info(f"[SESSION:{self.call_sid}] {tc.name} -> {result}")
                    get_tui().tool_call(tc.name, args, result)

                    self.messages.append({
                        "type": "function_call_output",
                        "call_id": tc.call_id,
                        "output": json.dumps(result),
                    })

                    if tc.name == "end_call":
                        self._ending_call = True

                # Follow-up LLM call — no tools, force a spoken response
                follow_up, _ = await llm_generate(
                    messages=self.messages,
                    on_token=self._on_llm_token,
                )
                full_text = follow_up

            if full_text:
                # Flush any remaining text in the sentence buffer
                remaining = self._sentence_buffer.flush()
                if remaining:
                    clean = sanitize_for_speech(remaining)
                    if clean and self.tts_client:
                        await self.tts_client.send_text(clean)

                await self.tts_client.flush()
                self.playback_tracker.set_full_text(full_text)
                debug_events.info(f"[SESSION:{self.call_sid}] ASSISTANT: {full_text}")
                get_tui().assistant_turn_finalized(full_text)

            # History commit happens later:
            #  - Normal: _on_playback_complete (when last mark ACKed)
            #  - Barge-in: _handle_flux_event (when user starts speaking)

            if self._ending_call:
                asyncio.create_task(self._end_call_after_delay())

        except asyncio.CancelledError:
            debug_events.info(f"[SESSION:{self.call_sid}] Reply generation cancelled (barge-in)")
        except Exception as e:
            logger.error(f"[SESSION:{self.call_sid}] Reply generation error: {e}")

    def _commit_assistant_response(self):
        """Add assistant response to conversation history.

        Normal completion: full text.
        Barge-in: only heard text + resumption context.
        """
        tracker = self.playback_tracker

        if tracker.was_interrupted():
            heard = tracker.get_heard_text()
            full = tracker.get_full_text()
            diag = tracker.debug_summary()
            debug_events.info(
                f"[SESSION:{self.call_sid}] COMMIT (interrupted): "
                f"marks={diag} heard={heard!r} full={full!r}"
            )
            get_tui().assistant_interrupted(heard=heard, full=full)

            if not heard:
                debug_events.info(f"[SESSION:{self.call_sid}] User heard nothing — skipping assistant message")
                return

            self.messages.append({"role": "assistant", "content": heard})

            if heard != full:
                self.messages.append({
                    "role": "system",
                    "content": (
                        f"Your previous response was interrupted by the caller. "
                        f"The caller heard only: \"{heard}\" "
                        f"Your full intended response was: \"{full}\" "
                        f"If the caller asks you to continue or repeat, "
                        f"pick up naturally from where they stopped hearing you."
                    ),
                })
                debug_events.info(f"[SESSION:{self.call_sid}] Injected resumption context")
        else:
            full = tracker.get_full_text()
            debug_events.info(f"[SESSION:{self.call_sid}] COMMIT (normal): full={full!r}")
            if full:
                self.messages.append({"role": "assistant", "content": full})

    async def _on_llm_token(self, token: str):
        """Called for each LLM text delta — buffer, sanitize, send to TTS."""
        logger.debug(f"[SESSION:{self.call_sid}] LLM token: {token!r}")
        get_tui().assistant_token(token)
        if not self.tts_client:
            return
        sentences = self._sentence_buffer.feed(token)
        for sentence in sentences:
            clean = sanitize_for_speech(sentence)
            if clean:
                await self.tts_client.send_text(clean + " ")

    # ------------------------------------------------------------------
    # End call handling
    # ------------------------------------------------------------------

    async def _end_call_after_delay(self):
        """Hang up after the goodbye audio finishes playing.

        We can't hang up the instant the LLM emits end_call — the goodbye
        sentence is still being synthesized and streamed. Wait for the
        last turn-end mark ACK from Twilio (the caller has heard the
        audio), then add a small tail so the final syllables don't clip.

        If we never sent a turn-end mark for this turn (unusual), fall
        back to a short bare sleep.
        """
        MARK_WAIT_TIMEOUT = 5.0   # hard cap for the mark ACK
        TAIL_PADDING = 0.5        # avoid clipping the last syllables
        FALLBACK_SLEEP = 3.0      # no turn-end mark? Give TTS time anyway

        turn_end_marks = [n for n in self._pending_marks if n.startswith("turn_end_")]
        if turn_end_marks:
            last_mark = max(turn_end_marks, key=lambda n: int(n.split("_")[-1]))
            logger.info(f"[SESSION:{self.call_sid}] Waiting for {last_mark} before hanging up")
            await self._wait_for_mark(last_mark, timeout=MARK_WAIT_TIMEOUT)
            await asyncio.sleep(TAIL_PADDING)
        else:
            await asyncio.sleep(FALLBACK_SLEEP)
        logger.info(f"[SESSION:{self.call_sid}] Ending call")

        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            try:
                from twilio.rest import Client as TwilioClient
                twilio = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                twilio.calls(self.call_sid).update(status="completed")
                logger.info(f"[SESSION:{self.call_sid}] Twilio call completed via REST")
            except Exception as e:
                logger.error(f"[SESSION:{self.call_sid}] Failed to end via Twilio REST: {e}")
        else:
            try:
                await self.twilio_ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Twilio audio output + marks
    # ------------------------------------------------------------------

    async def _send_audio_to_twilio(self, audio: bytes):
        """Send ulaw 8kHz audio to Twilio."""
        audio_b64 = base64.b64encode(audio).decode("utf-8")
        await self.twilio_ws.send_json({
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": audio_b64},
        })

    async def _send_mark(self, prefix: str = "audio") -> str:
        """Send a mark to Twilio and track it for ack.

        Returns the mark name. The corresponding Future in
        _pending_marks resolves when Twilio acks the mark
        (meaning audio up to this point has been played).
        """
        self._mark_counter += 1
        mark_name = f"{prefix}_{self._mark_counter}"
        future = asyncio.get_running_loop().create_future()
        self._pending_marks[mark_name] = future
        try:
            await self.twilio_ws.send_json({
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": mark_name},
            })
        except Exception as e:
            logger.debug(f"[SESSION:{self.call_sid}] Failed to send mark: {e}")
            self._pending_marks.pop(mark_name, None)
        return mark_name

    async def _wait_for_mark(self, mark_name: str, timeout: float = 10.0) -> bool:
        """Wait for a specific mark to be acked by Twilio.

        Returns True if acked, False on timeout or missing mark.
        """
        future = self._pending_marks.get(mark_name)
        if not future:
            return False
        try:
            await asyncio.wait_for(future, timeout=timeout)
            return True
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._pending_marks.pop(mark_name, None)
            return False

    def _cancel_pending_marks(self):
        """Cancel all pending mark futures (barge-in)."""
        for name, future in self._pending_marks.items():
            if not future.done():
                future.cancel()
        self._pending_marks.clear()

    async def _send_twilio_clear(self):
        """Tell Twilio to stop playing audio (barge-in)."""
        try:
            await self.twilio_ws.send_json({
                "event": "clear",
                "streamSid": self.stream_sid,
            })
        except Exception as e:
            logger.debug(f"[SESSION:{self.call_sid}] Failed to send clear: {e}")
