"""Central configuration — all values from env vars with sensible defaults."""

import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

# --- Server ---
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8080"))
SERVER_EXTERNAL_URL = os.getenv("SERVER_EXTERNAL_URL", "")

# --- API keys ---
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# --- Twilio (optional — only needed for programmatic hangup) ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# --- Flux STT ---
FLUX_WS_URL = os.getenv("FLUX_WS_URL", "wss://api.deepgram.com/v2/listen")
FLUX_MODEL = os.getenv("FLUX_MODEL", "flux-general-en")
FLUX_SAMPLE_RATE = int(os.getenv("FLUX_SAMPLE_RATE", "8000"))
FLUX_ENCODING = os.getenv("FLUX_ENCODING", "mulaw")
EOT_THRESHOLD = float(os.getenv("EOT_THRESHOLD", "0.7"))
EOT_TIMEOUT_MS = int(os.getenv("EOT_TIMEOUT_MS", "3000"))

# --- LLM ---
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# The agent speaks this line first, before the caller says anything.
# Kept short on purpose — sets the scene and invites a response.
GREETING = (
    "Thanks for calling Services Incorporated. How can I help you today?"
)

_SYSTEM_PROMPT_TEMPLATE = os.getenv("SYSTEM_PROMPT", (
    "You are the receptionist for Services, Inc., answering an inbound phone call. "
    "You help clients book, check on, and cancel appointments.\n\n"

    "TODAY'S DATE: {today}\n"
    "When the caller gives a relative date like 'tomorrow', 'next Tuesday', "
    "or 'sometime next week', resolve it against today's date before calling "
    "any tool. Never pass a date in the past to a tool.\n\n"

    "ABOUT SERVICES, INC.:\n"
    "Services, Inc. offers a range of consultations, assessments, and sessions "
    "tailored to client needs. If the caller asks what services you offer, "
    "what you do, or what's available, call get_services and relay the list "
    "back conversationally.\n\n"

    "VOICE RULES (your output is converted to speech verbatim by TTS):\n"
    "Use ONLY plain conversational text. "
    "NO markdown, no bold, no italics, no bullets, no numbered lists, no brackets, no stage directions. "
    "NO special characters for emphasis. "
    "Keep responses to two or three sentences per turn. "
    "Every response should end with a question or clear next step for the caller.\n\n"

    "INFORMATION ACCESS:\n"
    "You have INSTANT access to all scheduling information. "
    "NEVER say 'let me check', 'hold on', 'one moment', or 'please wait'. "
    "Speak as if the information is already in front of you. "
    "Say 'I can see there is availability on Monday at 2' not 'Let me check the system for you'.\n\n"

    "TOOL USE RULES:\n"
    "For read-only lookups (check_available_slots, check_appointment, get_services), just call them without asking. "
    "If the caller asks about availability without a specific date, call check_available_slots "
    "with NO date parameter — that returns the next upcoming slots. Do not guess a date. "
    "For state changes (book_appointment, cancel_appointment), confirm all details with the caller "
    "FIRST and wait for an explicit 'yes' before calling. "
    "When booking, use the exact slot_id returned by check_available_slots — never substitute a date. "
    "Call end_call ONLY after saying goodbye and confirming the caller has nothing else to address. "
    "Do not generate any text after calling end_call."
))


def get_system_prompt() -> str:
    """Build the system prompt with today's date injected.

    Called per-request (not at import) so a long-running process picks up
    date changes correctly across midnight.
    """
    today_str = date.today().strftime("%A, %B %d, %Y")
    return _SYSTEM_PROMPT_TEMPLATE.replace("{today}", today_str)

# --- ElevenLabs TTS ---
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "iP95p4xoKVk53GoZ742B")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
