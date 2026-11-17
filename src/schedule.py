"""Reminder scheduler. Persists to schedule.json next to the entry point."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

IST = timezone(timedelta(hours=5, minutes=30))

Kind = Literal["T-24h", "T-1h", "T-5min", "T+0", "T+2min"]

# For a "high" priority movie the full cascade fires.
CASCADE_HIGH: list[tuple[Kind, timedelta]] = [
    ("T-24h", timedelta(hours=-24)),
    ("T-1h", timedelta(hours=-1)),
    ("T-5min", timedelta(minutes=-5)),
    ("T+0", timedelta(0)),
    ("T+2min", timedelta(minutes=2)),
]
CASCADE_NORMAL: list[tuple[Kind, timedelta]] = [
    ("T-1h", timedelta(hours=-1)),
    ("T-5min", timedelta(minutes=-5)),
    ("T+0", timedelta(0)),
]


@dataclass
class Reminder:
    movie: str
    kind: Kind
    fire_at: str           # ISO datetime, IST
    booking_open_at: str   # ISO datetime, IST
    source_url: str
    sent: bool = False
    sent_at: str | None = None


@dataclass
class Schedule:
    reminders: list[Reminder] = field(default_factory=list)
    # tracks (movie, YYYY-MM-DD) that already fired an IMMEDIATE alarm
    immediate_fired: list[str] = field(default_factory=list)


def load(path: Path) -> Schedule:
    if not path.exists():
        return Schedule()
    data = json.loads(path.read_text())
    return Schedule(
        reminders=[Reminder(**r) for r in data.get("reminders", [])],
        immediate_fired=list(data.get("immediate_fired", [])),
    )


def save(path: Path, schedule: Schedule) -> None:
    data = {
        "reminders": [asdict(r) for r in schedule.reminders],
        "immediate_fired": schedule.immediate_fired,
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def add_or_update_schedule(
    schedule: Schedule,
    movie: str,
    booking_open_at: datetime,
    source_url: str,
    priority: str = "high",
) -> list[Reminder]:
    """Add the full reminder cascade for this (movie, booking_open_at). Returns
    the new reminders (empty if everything was already present and unchanged)."""
    booking_open_at = booking_open_at.astimezone(IST)
    cascade = CASCADE_HIGH if priority == "high" else CASCADE_NORMAL

    # If the existing schedule for this movie points to a different datetime,
    # remove the stale unsent ones and re-add fresh.
    existing = [r for r in schedule.reminders if r.movie == movie and not r.sent]
    existing_targets = {r.booking_open_at for r in existing}
    target_iso = booking_open_at.isoformat()
    if existing_targets and target_iso not in existing_targets:
        schedule.reminders = [
            r for r in schedule.reminders
            if not (r.movie == movie and not r.sent and r.booking_open_at != target_iso)
        ]

    created: list[Reminder] = []
    for kind, offset in cascade:
        fire_at = (booking_open_at + offset).isoformat()
        already = any(
            r.movie == movie
            and r.kind == kind
            and r.booking_open_at == target_iso
            for r in schedule.reminders
        )
        if already:
            continue
        rem = Reminder(
            movie=movie,
            kind=kind,
            fire_at=fire_at,
            booking_open_at=target_iso,
            source_url=source_url,
        )
        schedule.reminders.append(rem)
        created.append(rem)
    return created


def due_reminders(schedule: Schedule, now: datetime) -> list[Reminder]:
    now_ist = now.astimezone(IST).isoformat()
    return [
        r for r in schedule.reminders
        if not r.sent and r.fire_at <= now_ist
    ]


def mark_sent(schedule: Schedule, reminder: Reminder, now: datetime) -> None:
    reminder.sent = True
    reminder.sent_at = now.astimezone(IST).isoformat()


def should_fire_immediate(schedule: Schedule, movie: str, now: datetime) -> bool:
    key = f"{movie}|{now.astimezone(IST).date().isoformat()}"
    if key in schedule.immediate_fired:
        return False
    schedule.immediate_fired.append(key)
    return True
