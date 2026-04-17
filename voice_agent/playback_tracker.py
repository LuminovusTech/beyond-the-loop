"""PlaybackTracker — tracks what audio the caller actually heard.

When the agent speaks, TTS audio arrives in chunks. Each chunk gets a
Twilio "mark" that fires when playback reaches that point. On barge-in,
we use the last acked mark to determine how much text the caller heard,
then trim conversation history accordingly.
"""

from dataclasses import dataclass


@dataclass
class AudioSegment:
    mark_name: str
    text: str                       # "".join(alignment chars)
    duration_s: float               # len(audio_bytes) / 8000 for ulaw 8kHz
    char_start_times_ms: list[int]  # from normalizedAlignment
    chars_durations_ms: list[int]   # from normalizedAlignment
    chars: list[str]                # from normalizedAlignment
    acked: bool = False


class PlaybackTracker:
    """Tracks playback progress for a single agent turn."""

    def __init__(self):
        self._segments: list[AudioSegment] = []
        self._full_text: str = ""
        self._interrupted: bool = False

    def reset(self) -> None:
        """Clear all state for a new agent turn."""
        self._segments.clear()
        self._full_text = ""
        self._interrupted = False

    def add_segment(
        self,
        mark_name: str,
        audio_bytes: bytes,
        alignment_data: dict | None,
    ) -> None:
        """Register a TTS audio chunk with its alignment data."""
        if alignment_data:
            chars = alignment_data.get("chars", [])
            text = "".join(chars)
            # ElevenLabs uses camelCase keys
            char_start_times_ms = alignment_data.get("charStartTimesMs", [])
            chars_durations_ms = alignment_data.get("charDurationsMs", [])
        else:
            text = ""
            chars = []
            char_start_times_ms = []
            chars_durations_ms = []

        self._segments.append(AudioSegment(
            mark_name=mark_name,
            text=text,
            duration_s=len(audio_bytes) / 8000,
            char_start_times_ms=char_start_times_ms,
            chars_durations_ms=chars_durations_ms,
            chars=chars,
            acked=False,
        ))

    def ack_mark(self, mark_name: str) -> None:
        """Record that Twilio/mock phone played audio up to this mark."""
        for seg in self._segments:
            if seg.mark_name == mark_name:
                seg.acked = True
                return

    def set_full_text(self, text: str) -> None:
        """Set the full intended LLM response text."""
        self._full_text = text

    def mark_interrupted(self) -> None:
        """Called on barge-in. Freezes state for trimming."""
        self._interrupted = True

    def get_heard_text(self) -> str:
        """Concatenated text of acked segments. Empty if none acked.

        If no segments have alignment data (ElevenLabs stopped sending
        it — rate limit, connection hiccup, model change), we gracefully
        degrade to the naive "commit the full intended response" behavior
        instead of committing an empty string. Worse than what we'd get
        with alignment, but no worse than every other voice agent on the
        market.
        """
        # Check if ANY segment has alignment data
        has_alignment = any(seg.chars for seg in self._segments)
        if not has_alignment:
            return self._full_text

        # Iterate in order, stop at first un-acked segment
        parts = []
        for seg in self._segments:
            if seg.acked:
                parts.append(seg.text)
            else:
                break
        return "".join(parts)

    def get_full_text(self) -> str:
        """The full intended response."""
        return self._full_text

    def was_interrupted(self) -> bool:
        """Whether barge-in occurred during this turn."""
        return self._interrupted

    def debug_summary(self) -> dict:
        """Diagnostic snapshot for logging."""
        return {
            "acked": [s.mark_name for s in self._segments if s.acked],
            "total": [s.mark_name for s in self._segments],
            "interrupted": self._interrupted,
        }
