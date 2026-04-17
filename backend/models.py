"""Data models for the scheduling backend."""

from dataclasses import dataclass


@dataclass
class TimeSlot:
    slot_id: str
    date: str           # YYYY-MM-DD
    time: str           # HH:MM
    service_type: str   # "Initial consultation", "Standard assessment", etc.
    duration_min: int


@dataclass
class Client:
    name: str
    phone: str


@dataclass
class Appointment:
    appointment_id: str
    client: Client
    slot: TimeSlot
    booked_at: str      # ISO timestamp
