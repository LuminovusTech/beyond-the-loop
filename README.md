# Beyond the Loop: Voice Agent Reference Repo

A telephony voice agent built for the STL TechWeek 2026 DevLAB session
**"Beyond the Loop: Patterns for Production-Grade AI Voice Agents."**

This repo is deliberately **un-frameworked**: no Pipecat, no LiveKit, no
unified voice agent platform. It stitches together Deepgram Flux (STT) +
OpenAI GPT-4o mini (LLM) + ElevenLabs Turbo v2.5 (TTS) over a Twilio media stream so you can see
the moving parts and the seams between them. The point isn't to ship this
exact stack, but rather to understand what happens inside the ones that look
like a single box on a vendor's marketing diagram.

**This is a teaching repo.** Read it, run it, break it, experiment with it.

---

## What the agent does

It's a receptionist for a fictional business called **Services, Inc.** It
answers inbound calls and helps callers book, check, and cancel
appointments. The scenario is intentionally boring (a generic scheduling
flow with no domain quirks) so the patterns stay in the foreground.

Five tools are wired up:

- `check_available_slots`: list upcoming appointment slots
- `book_appointment`: book a specific slot for a caller
- `check_appointment`: look up an existing appointment
- `cancel_appointment`: cancel an existing appointment
- `end_call`: hang up after saying goodbye

The scheduling "database" is a mock in-memory service
(`backend/scheduling_service.py`). Swap it for a real one when you're
ready.

---

## Two patterns worth studying

Two production concerns the repo demonstrates, each meant to illustrate
a category of failure the naive STT → LLM → TTS loop doesn't handle on
its own:

1. **Spoken-output formatting.** LLMs are trained to emit markdown, lists,
   and emoji. Fine for screens, terrible for TTS. The repo combines
   prompt engineering with a deterministic sanitizer
   (`voice_agent/speech_filter.py`) that strips anything the prompt
   misses. Logs surface when the filter actually catches something so
   you can see the prompt escaping the cage.

2. **Accurate conversation history across barge-in.** If a caller
   interrupts halfway through an agent response, most voice agents still
   commit the entire intended response to the conversation history. The
   repo uses ElevenLabs character-level alignment timestamps plus Twilio
   mark ACKs to track what the caller *actually heard*
   (`voice_agent/playback_tracker.py`), then commits only the heard text
   plus a resumption hint so the LLM can pick up where the caller
   stopped it.

> A third pattern (accurate proper-noun / name capture) is discussed in
> the talk but not shipped in this repo. Doing it well is a talk of its
> own, and shipping a half-baked version would be worse than naming the
> problem and moving on.

---

## Quickstart

### Platform support

- **macOS / Linux**: supported directly.
- **Windows**: use **WSL2** and follow the Linux instructions. Native
  Windows may work but is not tested; the codebase leans on Unix-y
  behavior (signals, process groups) in a few places, and running inside
  WSL removes a whole class of "works on my machine" questions.

### 1. Clone and set up Python

```bash
git clone <repo-url>
cd beyond-the-loop

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Install zrok

[zrok](https://zrok.io) gives you a stable public URL so Twilio can reach
your laptop. No deployment needed.

```bash
# macOS
brew install zrok

# Linux / WSL
curl -sSf https://get.openziti.io/install.bash | sudo bash -s zrok

# Verify
zrok version
```

### 3. Create accounts

All five have free tiers. The wizard will ask for credentials, but it
goes faster with them ready.

| Service      | Sign up                                    | What you need                  |
|--------------|---------------------------------------------|--------------------------------|
| zrok         | [myzrok.io](https://myzrok.io)             | Enable token (from account page) |
| Deepgram     | [console.deepgram.com](https://console.deepgram.com/) | API key         |
| OpenAI       | [platform.openai.com](https://platform.openai.com/)   | API key         |
| ElevenLabs   | [elevenlabs.io](https://elevenlabs.io)     | API key                         |
| Twilio       | [console.twilio.com](https://console.twilio.com) | Account SID + Auth Token  |

### 4. Run the quickstart wizard

```bash
python setup/quickstart.py
```

The wizard writes your keys to `.env`, picks (or purchases) a Twilio
phone number, stands up a zrok reserved share, and points Twilio's voice
webhook at your tunnel. Config lands in `.env` and `.quickstart_state.json`.

### 5. Start the agent

```bash
python run.py
```

That starts the tunnel and the server together. Call your Twilio number.
`Ctrl+C` stops both.

If you're running your own public URL (ngrok, a deployed server,
whatever), skip the tunnel:

```bash
python run.py --no-tunnel
```

---

## Commands

| Command | What it does |
|---------|-------------|
| `python setup/quickstart.py` | First-time setup (API keys, Twilio, zrok, webhook wiring) |
| `python run.py` | Start tunnel + server together |
| `python run.py --no-tunnel` | Server only (bring your own URL) |
| `python run.py -v` | Show dev-iteration signals (barge-in, commit, resumption) |
| `python run.py -vv` | Raw firehose. Disables the TUI, dumps every log line |
| `python setup/quickstart.py --status` | Show current config and tunnel status |
| `python setup/quickstart.py --update-url` | Re-sync Twilio webhook to your tunnel URL |
| `python setup/quickstart.py --teardown` | Release tunnel, clear webhook, remove state |

---

## Project layout

```
beyond-the-loop/
  app.py                     Starlette entry point
  config.py                  Central config: env vars + system prompt builder
  run.py                     Start tunnel + server in one command
  requirements.txt
  .env.example               Template for environment variables

  setup/
    quickstart.py            First-time setup wizard

  telephony/
    routes.py                Twilio webhook + WebSocket handler

  voice_agent/
    session.py               VoiceAgentSession: the orchestrator
    stt.py                   Deepgram Flux WebSocket client
    llm.py                   OpenAI Responses API streaming client
    tts.py                   ElevenLabs streaming TTS client
    tools.py                 OpenAI tool/function definitions
    function_handlers.py     Dispatch layer (tool name -> backend call)
    speech_filter.py         Sentence buffering + output sanitization
    playback_tracker.py      Track what the caller actually heard
    tui.py                   Rich live-console TUI
    logging_setup.py         Verbosity tiers

  backend/
    models.py                Data classes (Slot, Client, Appointment)
    scheduling_service.py    Mock in-memory scheduling backend

  mock_phone/                Browser-based dev phone (see below)
```

---

## Development without a phone

`mock_phone/` is a static web page that simulates Twilio's media stream
over WebSocket: mic in, audio out, same protocol. Handy for iterating
without dialing in.

Once the server is running, open `http://localhost:8080/mock-phone` in a
browser.

---

## Try it

Once you're on a call with the agent, these prompts surface the two
patterns so you can see them work (run with `-v` to watch the signals
in the TUI):

- **Spoken-output filter.** Ask *"what services do you offer?"* The
  LLM wants to answer with a bulleted list; the filter converts it to
  prose before TTS and logs a warning when it catches something. Any
  question that invites a list works (services, hours, locations).
- **Barge-in resumption.** Let the agent start reading a list of
  available slots, interrupt it halfway with *"wait, what was the
  second one?"* It resumes from what you actually heard, not from
  where it thought it was. Watch the TUI for `COMMIT (interrupted)`
  showing `heard` vs. `full`.

---

## Troubleshooting

- **`zrok is not installed`** – see step 2. Make sure `zrok` is on your PATH.

- **`zrok enable failed`** – use the enable token from your
[myzrok.io](https://myzrok.io) account page, not your password.

**Twilio plays a disclaimer before connecting**: you're on a trial
account. Upgrade at [console.twilio.com](https://console.twilio.com) to
remove it. The agent still works with the disclaimer.

**Tunnel is up but calls don't connect**: make sure both processes are
running (`python run.py` handles both). Check that `SERVER_PORT` in
`.env` matches the port the tunnel is pointing at (default 8080).

**Agent talks over itself / won't stop**: bump verbosity with
`python run.py -v` to see the barge-in and mark-tracking signals. The
playback tracker needs Twilio mark ACKs to figure out what was heard.

---

## License

MIT. Use it however you like.
