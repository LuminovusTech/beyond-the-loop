"""Mock in-memory scheduling service for Services, Inc.

Pre-generates available slots for the next 5 business days and seeds
a couple of fake client appointments for demo purposes.
"""

import logging
import uuid
from datetime import date, datetime, timedelta

from backend.models import Appointment, Client, TimeSlot

logger = logging.getLogger(__name__)


SERVICE_TYPES = [
    ("Initial consultation", 60),
    ("Standard assessment", 30),
    ("Follow-up session", 30),
    ("Extended session", 60),
]


class SchedulingService:
    def __init__(self):
        self._slots: dict[str, TimeSlot] = {}
        self._appointments: dict[str, Appointment] = {}
        self._generate_slots()
        self._seed_demo_data()

    # ------------------------------------------------------------------
    # Slot generation
    # ------------------------------------------------------------------

    def _generate_slots(self):
        """Generate available slots for the next 5 business days."""
        today = datetime.now().date()
        days_generated = 0
        current = today + timedelta(days=1)  # start tomorrow

        while days_generated < 5:
            weekday = current.weekday()  # 0=Mon, 6=Sun

            if weekday == 6:  # Sunday — closed
                current += timedelta(days=1)
                continue

            if weekday == 5:  # Saturday 10am-2pm
                start_hour, end_hour = 10, 14
            else:  # Mon-Fri 9am-5pm
                start_hour, end_hour = 9, 17

            hour = start_hour
            while hour < end_hour:
                for service_type, duration in SERVICE_TYPES:
                    if hour + duration / 60 > end_hour:
                        continue
                    slot_id = str(uuid.uuid4())[:8]
                    self._slots[slot_id] = TimeSlot(
                        slot_id=slot_id,
                        date=current.isoformat(),
                        time=f"{hour:02d}:00",
                        service_type=service_type,
                        duration_min=duration,
                    )
                hour += 1

            days_generated += 1
            current += timedelta(days=1)

    def _seed_demo_data(self):
        """Pre-seed a couple of appointments for demo flow."""
        # Pick two real slots and book them
        available = list(self._slots.values())
        if len(available) < 2:
            return

        demo_clients = [
            Client(name="Jordan Smith", phone="555-123-4567"),
            Client(name="Alex Lee", phone="555-987-6543"),
        ]

        for i, client in enumerate(demo_clients):
            slot = available[i]
            appt_id = f"APPT-{str(uuid.uuid4())[:6].upper()}"
            self._appointments[appt_id] = Appointment(
                appointment_id=appt_id,
                client=client,
                slot=slot,
                booked_at=datetime.now().isoformat(),
            )
            # Remove booked slot from available pool
            del self._slots[slot.slot_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_available_slots(self, date: str | None = None) -> dict:
        """Return available slots, optionally filtered by date.

        If the model passes a date in the past (a common LLM failure mode
        when its training data anchors it to an older "today"), fall back
        to returning upcoming slots and flag it in the response. Empty
        results because of a stale date cause confusing conversations —
        the agent ends up asking the caller to pick a date the agent is
        already supposed to know.
        """
        slots = list(self._slots.values())
        today = self._today_iso()
        filtered_past = False

        if date:
            if date < today:
                logger.warning(
                    f"[SCHEDULING] Ignoring past date {date!r} "
                    f"(today={today}) — returning upcoming availability"
                )
                filtered_past = True
            else:
                slots = [s for s in slots if s.date == date]

        # Sort by date then time
        slots.sort(key=lambda s: (s.date, s.time))

        # Limit to 6 slots to keep LLM response manageable
        slots = slots[:6]

        result: dict = {
            "status": "ok",
            "today": today,
            "slots": [
                {
                    "slot_id": s.slot_id,
                    "date": s.date,
                    "time": s.time,
                    "service_type": s.service_type,
                    "duration_min": s.duration_min,
                }
                for s in slots
            ],
            "total_available": len(self._slots),
        }
        if filtered_past:
            result["note"] = (
                f"The requested date {date} is in the past. "
                f"Showing next upcoming availability instead."
            )
        return result

    @staticmethod
    def _today_iso() -> str:
        return date.today().isoformat()

    async def book_appointment(
        self, client_name: str, client_phone: str, slot_id: str
    ) -> dict:
        """Book an appointment for the given slot."""
        slot = self._slots.get(slot_id)
        if not slot:
            return {"status": "error", "message": "Slot not found or already booked."}

        appt_id = f"APPT-{str(uuid.uuid4())[:6].upper()}"
        client = Client(name=client_name, phone=client_phone)
        appointment = Appointment(
            appointment_id=appt_id,
            client=client,
            slot=slot,
            booked_at=datetime.now().isoformat(),
        )

        self._appointments[appt_id] = appointment
        del self._slots[slot_id]

        return {
            "status": "booked",
            "appointment_id": appt_id,
            "date": slot.date,
            "time": slot.time,
            "service_type": slot.service_type,
        }

    async def check_appointment(
        self, client_name: str | None = None, client_phone: str | None = None
    ) -> dict:
        """Look up appointments by client name or phone."""
        matches = []
        for appt in self._appointments.values():
            name_match = (
                client_name
                and client_name.lower() in appt.client.name.lower()
            )
            phone_match = (
                client_phone
                and client_phone in appt.client.phone
            )
            if name_match or phone_match:
                matches.append({
                    "appointment_id": appt.appointment_id,
                    "client_name": appt.client.name,
                    "date": appt.slot.date,
                    "time": appt.slot.time,
                    "service_type": appt.slot.service_type,
                })

        if not matches:
            return {"status": "ok", "message": "No appointments found.", "appointments": []}

        return {"status": "ok", "appointments": matches}

    async def cancel_appointment(self, appointment_id: str) -> dict:
        """Cancel an appointment and return the slot to the pool."""
        appt = self._appointments.get(appointment_id)
        if not appt:
            return {"status": "error", "message": "Appointment not found."}

        # Return slot to available pool
        self._slots[appt.slot.slot_id] = appt.slot
        del self._appointments[appointment_id]

        return {
            "status": "cancelled",
            "appointment_id": appointment_id,
            "message": "Appointment has been cancelled.",
        }


# Singleton instance — created once at import time
scheduling_service = SchedulingService()
