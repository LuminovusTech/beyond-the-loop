"""List of services offered by Services, Inc.

Returned by the `get_services` tool. The strings here are deliberately
packed with markdown, emoji, bullet characters, and other formatting
hazards so the spoken-output filter has something to catch when the LLM
echoes this content back into its spoken response.

This mirrors a real-world failure mode: CMS content, wiki fields, Notion
API responses, and markdown-formatted database columns routinely return
formatted strings that a naive voice agent will speak verbatim.
"""


SERVICES = [
    "**Standard consultation** \u2b50",
    "*Premium* consultation (most popular!)",
    "- Follow-up session\n- Extended session\n- Custom package",
    "Assessment \U0001f4cb \u2014 quick intake & plan",
    "`Specialty` session \U0001f389 **limited availability**",
]


async def get_services() -> dict:
    """Return the list of services. Intentionally formatting-hazardous."""
    return {"services": SERVICES}
