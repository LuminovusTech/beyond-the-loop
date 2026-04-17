"""OpenAI function definitions for the voice agent's tool set.

Uses Responses API format (flat schema, not Chat Completions wrapper).
"""

TOOLS = [
    {
        "type": "function",
        "name": "check_available_slots",
        "description": (
            "Check available appointment slots at Services, Inc. "
            "Call this when a client asks about availability. "
            "Read-only lookup, no confirmation needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format. Omit to see all upcoming availability.",
                }
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "book_appointment",
        "description": (
            "Book an appointment. MUST have a slot_id from check_available_slots. "
            "Confirm details with client FIRST, wait for explicit 'yes', THEN call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string", "description": "Full name"},
                "client_phone": {"type": "string", "description": "Phone number"},
                "slot_id": {"type": "string", "description": "slot_id from check_available_slots"},
            },
            "required": ["client_name", "client_phone", "slot_id"],
        },
    },
    {
        "type": "function",
        "name": "check_appointment",
        "description": "Look up a client's existing appointment by name or phone. Read-only lookup.",
        "parameters": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string"},
                "client_phone": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "cancel_appointment",
        "description": (
            "Cancel an existing appointment. Confirm with client FIRST, "
            "wait for explicit confirmation, THEN call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "From check_appointment results",
                }
            },
            "required": ["appointment_id"],
        },
    },
    {
        "type": "function",
        "name": "get_services",
        "description": (
            "List the services Services, Inc. offers. "
            "Call this when a caller asks what services you provide, "
            "what you do, or what's available. Read-only lookup."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "end_call",
        "description": (
            "End the call. Say goodbye FIRST, then call this. "
            "Do not generate text after calling this."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": ["appointment_booked", "customer_goodbye", "no_action_needed"],
                }
            },
            "required": ["reason"],
        },
    },
]
