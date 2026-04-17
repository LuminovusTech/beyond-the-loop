"""Speech filter — deterministic safety net for LLM output.

LLMs are trained to format for screens: markdown, emoji, bullet points,
stage directions. TTS reads all of that literally. The system prompt asks
the model not to, but it slips. This filter catches what the prompt misses.

Architecture:
  LLM tokens → SentenceBuffer → sanitize_for_speech() → TTS.send_text()
"""

import logging
import re

logger = logging.getLogger(__name__)


# -- Sentence buffering ------------------------------------------------

class SentenceBuffer:
    """Accumulate LLM tokens and emit complete sentences.

    ElevenLabs needs meaningful text chunks (~50+ chars) to produce
    natural prosody. This buffers tokens and splits on sentence-ending
    punctuation so each chunk sent to TTS is a full sentence.
    """

    def __init__(self):
        self.buffer = ""

    def feed(self, token: str) -> list[str]:
        """Feed a token, return list of complete sentences (if any)."""
        self.buffer += token
        sentences = []
        while match := re.search(r'[.!?]\s', self.buffer):
            end = match.end()
            sentence = self.buffer[:end].strip()
            if sentence:
                sentences.append(sentence)
            self.buffer = self.buffer[end:]
        return sentences

    def flush(self) -> str | None:
        """Return any remaining text at end of stream."""
        remaining = self.buffer.strip()
        self.buffer = ""
        return remaining if remaining else None


# -- Sanitization chain ------------------------------------------------

def sanitize_for_speech(text: str) -> str:
    """Run all sanitization steps in order. Returns cleaned text.

    Logs a warning whenever the filter actually changes something —
    that means the prompt failed and the safety net caught it.
    """
    original = text
    text = strip_markdown(text)
    text = strip_stage_directions(text)
    text = strip_emoji(text)
    text = strip_special_characters(text)
    text = collapse_whitespace(text)
    text = text.strip()

    if text != original.strip():
        logger.warning(
            f"[SPEECH_FILTER] Sanitized LLM output:\n"
            f"  before: {original.strip()!r}\n"
            f"  after:  {text!r}"
        )

    return text


def strip_markdown(text: str) -> str:
    """Remove markdown formatting that TTS would read literally."""
    # Code fences (``` ... ```) — remove entirely
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Inline code (`code`) — keep the content
    text = re.sub(r'`([^`]*)`', r'\1', text)
    # Bold/italic markers: **text**, *text*, __text__, _text_
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', text)
    # Headers: ### Title -> Title
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Markdown links: [text](url) -> text
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Bullet points: - item, * item -> item
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
    # Numbered lists: 1. item, 2) item -> item
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.MULTILINE)
    return text


def strip_stage_directions(text: str) -> str:
    """Remove bracketed stage directions and action parentheticals."""
    # [pause], [warmly], [checking system] — single-word or short phrases
    text = re.sub(r'\[(?:pause|wait|silence|thinking|laughs?|sighs?|clears? throat|warmly|softly|gently|cheerfully|checking[^]]*|looking[^]]*)\]', '', text, flags=re.IGNORECASE)
    # (sighs), (thinking), (checking system) — action parentheticals
    # But preserve legitimate parentheticals like (the one on Main Street)
    text = re.sub(r'\((?:sighs?|thinking|pause|checking[^)]*|looking[^)]*|laughs?|clears? throat)\)', '', text, flags=re.IGNORECASE)
    return text


def strip_emoji(text: str) -> str:
    """Remove emoji characters."""
    emoji_pattern = re.compile(
        '['
        '\U0001F600-\U0001F64F'  # emoticons
        '\U0001F300-\U0001F5FF'  # symbols & pictographs
        '\U0001F680-\U0001F6FF'  # transport & map
        '\U0001F1E0-\U0001F1FF'  # flags
        '\U00002702-\U000027B0'  # dingbats
        '\U000024C2-\U0001F251'  # misc
        '\U0001F900-\U0001F9FF'  # supplemental
        '\U0001FA00-\U0001FA6F'  # chess symbols
        '\U0001FA70-\U0001FAFF'  # symbols extended
        '\U00002600-\U000026FF'  # misc symbols
        '\U0000FE00-\U0000FE0F'  # variation selectors
        '\U0000200D'             # zero width joiner
        '\U0000200B-\U0000200F'  # zero width spaces
        ']+',
        flags=re.UNICODE,
    )
    return emoji_pattern.sub('', text)


def strip_special_characters(text: str) -> str:
    """Remove special characters that aren't normal speech punctuation.

    Keeps: letters, digits, basic punctuation (. , ! ? ' " - : ;)
    and common spoken symbols ($ % & @ /).
    """
    text = re.sub(r'[#~|\\<>{}[\]^`]+', '', text)
    return text


def collapse_whitespace(text: str) -> str:
    """Normalize whitespace — multiple spaces to one, strip edges."""
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()
