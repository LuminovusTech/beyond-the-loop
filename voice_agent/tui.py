"""Live transcript TUI using Rich.

A module-level singleton drives a Rich ``Live`` display that shows the
conversation as it unfolds:

  🧑  Hello. Can you hear me?                     ← live-rewrites from Flux Updates
  🤖  Thanks for calling Services Inc…            ← streams in from LLM tokens
  🔧  check_available_slots({"date": ""})         ← tool call + pretty-printed result
  🧑  Yeah, I'm just curious what you do.
  🤖  We provide a range of consultations ┤ (interrupted) ̶a̶s̶s̶e̶s̶s̶m̶e̶n̶t̶s̶…

Call-site integration is deliberately simple — every hook is a no-op
when the TUI is disabled (at ``-vv`` verbosity), so session.py doesn't
need conditionals.  Add a new event? Add a new method.  Nothing fancy.

External log lines (uvicorn access, init, warnings, -v debug_events)
are routed through a ``RichHandler`` so they don't tear the Live
display.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, Group
from rich.json import JSON
from rich.live import Live
from rich.logging import RichHandler
from rich.padding import Padding
from rich.text import Text

from voice_agent.logging_setup import tui_enabled


# ---------------------------------------------------------------------------
# Turn model
# ---------------------------------------------------------------------------

@dataclass
class _UserTurn:
    text: str = ""
    finalized: bool = False


@dataclass
class _AssistantTurn:
    full_text: str = ""       # all tokens we've seen from the LLM
    spoken_prefix: str = ""   # portion the caller actually heard (on barge-in)
    interrupted: bool = False
    finalized: bool = False


@dataclass
class _ToolTurn:
    name: str
    args: dict
    result: Any


_Turn = _UserTurn | _AssistantTurn | _ToolTurn


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

_USER_STYLE = "bold cyan"
_ASSISTANT_STYLE = "bold green"
_TOOL_STYLE = "bold yellow"
_INTERRUPT_MARK_STYLE = "bold red"
_UNSPOKEN_STYLE = "dim strike"
_META_STYLE = "dim"
_USER_PREFIX = "🧑 "
_ASSISTANT_PREFIX = "🤖 "
_TOOL_PREFIX = "🔧 "


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class TranscriptView:
    """Active TUI — maintains a transcript and re-renders on every event."""

    def __init__(self) -> None:
        self.console = Console()
        self._turns: list[_Turn] = []
        self._lock = threading.Lock()
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=10,
            auto_refresh=True,
            transient=False,
            vertical_overflow="visible",
        )
        self._started = False

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._live.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        try:
            self._live.stop()
        except Exception:
            pass

    # -- event hooks (called from session.py) --------------------------

    def call_started(self, call_sid: str) -> None:
        # Clear the transcript for the new call and print a one-time marker
        # above the Live region (not as a persistent turn — Live's re-renders
        # would print the marker repeatedly).
        with self._lock:
            self._turns.clear()
        self._print_above(
            Text(f"  ─── call started · {call_sid} ───", style=_META_STYLE)
        )
        self._refresh()

    def call_ended(self, call_sid: str) -> None:
        # Stop Live to freeze the transcript into scrollback, then print
        # the end marker BELOW the transcript (not above, where Rich's
        # Live contract puts it).  Restart Live afterward so the next
        # call can render normally.
        with self._lock:
            had_turns = bool(self._turns)
            self._turns.clear()
        if self._started:
            try:
                self._live.stop()
            except Exception:
                pass
        if had_turns:
            try:
                # Live.stop() doesn't add a trailing newline, so start
                # with one to keep the end marker on its own line.
                self.console.print()
                self.console.print(
                    Text(f"  ─── call ended · {call_sid} ───", style=_META_STYLE)
                )
                self.console.print()  # blank line between calls
            except Exception:
                pass
        if self._started:
            # Keep ``self._started`` True; just restart the Live handle
            # with an empty render for the next call.
            try:
                self._live = Live(
                    self._render(),
                    console=self.console,
                    refresh_per_second=10,
                    auto_refresh=True,
                    transient=False,
                    vertical_overflow="visible",
                )
                self._live.start()
            except Exception:
                pass

    def _print_above(self, renderable) -> None:
        """Print a one-shot line above the Live region."""
        if self._started:
            try:
                self.console.print(renderable)
            except Exception:
                pass

    def user_turn_started(self, transcript: str) -> None:
        with self._lock:
            turn = self._current_user_turn()
            if turn is None or turn.finalized:
                self._turns.append(_UserTurn(text=transcript))
            else:
                turn.text = transcript
        self._refresh()

    def user_turn_updated(self, transcript: str) -> None:
        with self._lock:
            turn = self._current_user_turn()
            if turn is None or turn.finalized:
                self._turns.append(_UserTurn(text=transcript))
            else:
                turn.text = transcript
        self._refresh()

    def user_turn_finalized(self, transcript: str) -> None:
        with self._lock:
            turn = self._current_user_turn()
            if turn is None or turn.finalized:
                self._turns.append(_UserTurn(text=transcript, finalized=True))
            else:
                turn.text = transcript
                turn.finalized = True
        self._refresh()

    def assistant_token(self, token: str) -> None:
        with self._lock:
            turn = self._current_assistant_turn()
            if turn is None or turn.finalized or turn.interrupted:
                turn = _AssistantTurn()
                self._turns.append(turn)
            turn.full_text += token
        self._refresh()

    def assistant_turn_finalized(self, full_text: str) -> None:
        with self._lock:
            turn = self._current_assistant_turn()
            if turn is None or turn.interrupted:
                self._turns.append(_AssistantTurn(
                    full_text=full_text, finalized=True
                ))
            else:
                turn.full_text = full_text
                turn.finalized = True
        self._refresh()

    def assistant_interrupted(self, heard: str, full: str) -> None:
        """Called on barge-in — split assistant line at heard/full boundary."""
        with self._lock:
            turn = self._current_assistant_turn()
            if turn is None:
                turn = _AssistantTurn(full_text=full)
                self._turns.append(turn)
            else:
                turn.full_text = full or turn.full_text
            turn.spoken_prefix = (heard or "").strip()
            turn.interrupted = True
            turn.finalized = True
        self._refresh()

    def tool_call(self, name: str, args: dict, result: Any) -> None:
        with self._lock:
            self._turns.append(_ToolTurn(name=name, args=args, result=result))
        self._refresh()

    # -- internals ------------------------------------------------------

    def _current_user_turn(self) -> _UserTurn | None:
        for turn in reversed(self._turns):
            if isinstance(turn, _UserTurn):
                return turn
            if isinstance(turn, (_AssistantTurn, _ToolTurn)):
                return None
        return None

    def _current_assistant_turn(self) -> _AssistantTurn | None:
        for turn in reversed(self._turns):
            if isinstance(turn, _AssistantTurn):
                return turn
            if isinstance(turn, (_UserTurn, _ToolTurn)):
                return None
        return None

    def _refresh(self) -> None:
        if self._started:
            try:
                self._live.update(self._render())
            except Exception:
                pass

    def _render(self) -> Group:
        renderables = []
        for turn in self._turns:
            rendered = _render_turn(turn)
            if rendered is not None:
                renderables.append(rendered)
        # Empty Group renders as zero height — the Live region stays
        # invisible until there's something to show.
        return Group(*renderables)


def _render_turn(turn: _Turn):
    if isinstance(turn, _UserTurn):
        text = Text()
        text.append(_USER_PREFIX)
        text.append(turn.text or " ", style=_USER_STYLE)
        if not turn.finalized:
            text.append(" ▍", style=_USER_STYLE)
        return text

    if isinstance(turn, _AssistantTurn):
        text = Text()
        text.append(_ASSISTANT_PREFIX)
        if turn.interrupted and turn.spoken_prefix:
            spoken, unspoken = _split_on_spoken_prefix(
                turn.full_text, turn.spoken_prefix,
            )
            text.append(spoken.rstrip(), style=_ASSISTANT_STYLE)
            if unspoken.strip():
                text.append(" ┤ ", style=_INTERRUPT_MARK_STYLE)
                text.append("(interrupted) ", style=_INTERRUPT_MARK_STYLE)
                text.append(unspoken.lstrip(), style=_UNSPOKEN_STYLE)
        elif turn.interrupted:
            # caller heard nothing — the whole line was cut
            text.append("(interrupted before any audio played) ",
                       style=_INTERRUPT_MARK_STYLE)
            if turn.full_text:
                text.append(turn.full_text, style=_UNSPOKEN_STYLE)
        else:
            text.append(turn.full_text or " ", style=_ASSISTANT_STYLE)
            if not turn.finalized:
                text.append(" ▍", style=_ASSISTANT_STYLE)
        return text

    if isinstance(turn, _ToolTurn):
        header = Text()
        header.append(_TOOL_PREFIX)
        args_repr = json.dumps(turn.args) if turn.args else "{}"
        header.append(f"{turn.name}({args_repr})", style=_TOOL_STYLE)
        try:
            body = JSON.from_data(turn.result, indent=2)
        except Exception:
            body = Text(repr(turn.result), style=_META_STYLE)
        return Group(header, Padding(body, (0, 0, 0, 4)))

    return None


def _split_on_spoken_prefix(full: str, spoken: str) -> tuple[str, str]:
    """Find where the heard text ends inside the full text.

    ``spoken`` comes from character-level TTS alignment and may have
    slightly different whitespace than ``full``, so we do a tolerant
    character-by-character walk instead of a plain string find.
    """
    full = full or ""
    spoken = (spoken or "").strip()
    if not spoken:
        return "", full
    i = 0
    j = 0
    while i < len(full) and j < len(spoken):
        if full[i] == spoken[j]:
            i += 1
            j += 1
        elif full[i].isspace():
            i += 1
        elif spoken[j].isspace():
            j += 1
        else:
            break
    # Consume any trailing whitespace in full so the split lands cleanly
    while i < len(full) and full[i].isspace():
        i += 1
    return full[:i], full[i:]


# ---------------------------------------------------------------------------
# Null TUI (used at -vv, where we want raw logs)
# ---------------------------------------------------------------------------

class _NullTUI:
    """No-op TUI — every hook is a pass so session.py stays unconditional."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def call_started(self, call_sid: str) -> None:
        pass

    def call_ended(self, call_sid: str) -> None:
        pass

    def user_turn_started(self, transcript: str) -> None:
        pass

    def user_turn_updated(self, transcript: str) -> None:
        pass

    def user_turn_finalized(self, transcript: str) -> None:
        pass

    def assistant_token(self, token: str) -> None:
        pass

    def assistant_turn_finalized(self, full_text: str) -> None:
        pass

    def assistant_interrupted(self, heard: str, full: str) -> None:
        pass

    def tool_call(self, name: str, args: dict, result: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Singleton + install
# ---------------------------------------------------------------------------

_instance: TranscriptView | _NullTUI | None = None


def get_tui() -> TranscriptView | _NullTUI:
    global _instance
    if _instance is None:
        _instance = _NullTUI()
    return _instance


def install(force_disable: bool = False) -> TranscriptView | _NullTUI:
    """Install the TUI and redirect logging through Rich.

    Call once at startup after logging_setup.configure().  If
    ``force_disable`` is True or ``tui_enabled()`` is False, installs a
    no-op TUI and leaves logging alone.
    """
    global _instance

    if force_disable or not tui_enabled():
        _instance = _NullTUI()
        return _instance

    tui = TranscriptView()
    tui.start()
    _instance = tui

    _reconfigure_logging_for_tui(tui.console)
    return tui


def _reconfigure_logging_for_tui(console: Console) -> None:
    """Swap the root logging handler for a RichHandler tied to our console.

    Without this, uvicorn/httpx/etc. write to stderr directly and their
    lines tear through the Live display.  RichHandler integrates with
    Live so log lines appear above the transcript cleanly.

    Uvicorn is tricky: it configures its own loggers (`uvicorn`,
    `uvicorn.error`, `uvicorn.access`) during ``uvicorn.run()`` — AFTER
    this function runs.  So we clear their handlers AND set propagate=True
    so they bubble up to the root Rich handler instead.
    """
    root = logging.getLogger()
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
        markup=False,
    )
    # Replace any existing handlers so we don't double-log.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(rich_handler)

    # Uvicorn adds its own handlers late.  Force its named loggers to
    # propagate instead — we'll also clear them once uvicorn has set up.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # Patch logging.Logger.addHandler to strip handlers uvicorn adds
    # after this point for those three loggers.
    _shield_uvicorn_loggers()


def _shield_uvicorn_loggers() -> None:
    """Prevent uvicorn from re-attaching its own handlers after install.

    Uvicorn configures its loggers inside ``uvicorn.run()`` — after our
    install() has run.  We wrap ``Logger.addHandler`` so any handler
    added to a uvicorn logger is silently dropped; those loggers keep
    propagating to root (our RichHandler).
    """
    _UVICORN_NAMES = {"uvicorn", "uvicorn.error", "uvicorn.access"}
    original_add_handler = logging.Logger.addHandler

    def guarded_add_handler(self, hdlr):  # type: ignore[no-redef]
        if self.name in _UVICORN_NAMES:
            # Keep propagation on so records still reach our root handler.
            self.propagate = True
            return
        return original_add_handler(self, hdlr)

    logging.Logger.addHandler = guarded_add_handler  # type: ignore[assignment]
