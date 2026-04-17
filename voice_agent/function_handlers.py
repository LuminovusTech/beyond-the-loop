"""Function dispatch — routes tool calls to backend handlers."""

import logging

logger = logging.getLogger(__name__)


async def dispatch_function(name: str, args: dict) -> dict:
    """Route a function call to the appropriate backend handler."""
    from backend.scheduling_service import scheduling_service

    logger.debug(f"[FUNC] Dispatching {name} with args={args}")

    match name:
        case "check_available_slots":
            return await scheduling_service.get_available_slots(
                date=args.get("date")
            )
        case "book_appointment":
            return await scheduling_service.book_appointment(
                client_name=args["client_name"],
                client_phone=args["client_phone"],
                slot_id=args["slot_id"],
            )
        case "check_appointment":
            return await scheduling_service.check_appointment(
                client_name=args.get("client_name"),
                client_phone=args.get("client_phone"),
            )
        case "cancel_appointment":
            return await scheduling_service.cancel_appointment(
                appointment_id=args["appointment_id"],
            )
        case "get_services":
            from backend.services import get_services
            return await get_services()
        case "end_call":
            return {"status": "call_ended", "reason": args.get("reason")}
        case _:
            return {"error": f"Unknown function: {name}"}
