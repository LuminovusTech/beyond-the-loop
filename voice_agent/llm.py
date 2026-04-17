"""LLM streaming client — OpenAI Responses API.

Streams responses and forwards text deltas to a callback (which typically
feeds into TTS). Accumulates function call arguments from streaming events.
Cancellation is via asyncio task cancellation — the caller cancels the
task and we let CancelledError propagate.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import openai

from config import LLM_MODEL, OPENAI_API_KEY, get_system_prompt
from voice_agent.logging_setup import get_debug_events_logger

logger = logging.getLogger(__name__)
debug_events = get_debug_events_logger()

_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)


async def generate(
    messages: list,
    on_token: Callable[[str], Awaitable[None]],
    tools: list[dict] | None = None,
) -> tuple[str, list]:
    """Stream a response, forwarding text deltas to on_token.

    Args:
        messages: Conversation history (Responses API input format).
        on_token: Async callback invoked with each text delta.
        tools: Tool definitions. If None, no tools are offered.

    Returns:
        (full_text, output_items) — output_items contains any function_call
        items the model emitted. Empty list if pure text response.
    """
    full_text = ""
    output_items: list = []
    arg_buffers: dict[str, dict] = {}

    create_kwargs: dict = {
        "model": LLM_MODEL,
        "instructions": get_system_prompt(),
        "input": messages,
        "stream": True,
    }
    if tools:
        create_kwargs["tools"] = tools
        create_kwargs["tool_choice"] = "auto"

    try:
        stream = await _client.responses.create(**create_kwargs)

        async for event in stream:
            etype = event.type

            if etype == "response.output_item.added":
                output_items.append(event.item)
                if event.item.type == "function_call":
                    arg_buffers[event.item.id] = {
                        "item": event.item,
                        "arguments": "",
                    }

            elif etype == "response.output_text.delta":
                full_text += event.delta
                await on_token(event.delta)

            elif etype == "response.function_call_arguments.delta":
                buf = arg_buffers.get(event.item_id)
                if buf is not None:
                    buf["arguments"] += event.delta

            elif etype == "response.function_call_arguments.done":
                buf = arg_buffers.pop(event.item_id, None)
                if buf is not None:
                    buf["item"].arguments = event.arguments

    except asyncio.CancelledError:
        logger.debug("[LLM] Task cancelled")
        raise
    except Exception as e:
        logger.error(f"[LLM] Error during generation: {e}")
        raise

    # Filter to just function_call items
    tool_calls = [i for i in output_items if getattr(i, "type", None) == "function_call"]

    if tool_calls:
        debug_events.info(f"[LLM] Tool calls: {[tc.name for tc in tool_calls]}")

    return full_text, output_items
