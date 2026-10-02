"""Spend ledger with reservations. Thread-safe so two concurrent agent calls cannot both pass a limit."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


def utc_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


@dataclass
class Entry:
    id: int
    day: str
    merchant: str
    cents: int
    state: str  # "reserved" | "committed" | "released"


class Ledger:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: list[Entry] = []
        self._next = 1

    @property
    def lock(self):
        return self._lock

    def _live(self, day):
        return [e for e in self._entries if e.day == day and e.state in ("reserved", "committed")]

    def spent_today(self) -> int:
        with self._lock:
            return sum(e.cents for e in self._live(utc_day(self._clock())))

    def spent_today_at(self, merchant: str) -> int:
        with self._lock:
            return sum(e.cents for e in self._live(utc_day(self._clock())) if e.merchant == merchant)

    def reserve(self, merchant: str, cents: int) -> int:
        with self._lock:
            e = Entry(self._next, utc_day(self._clock()), merchant, cents, "reserved")
            self._next += 1
            self._entries.append(e)
            return e.id

    def _set(self, entry_id: int, state: str):
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    e.state = state
                    return
            raise KeyError(entry_id)

    def commit(self, entry_id: int):
        self._set(entry_id, "committed")

    def release(self, entry_id: int):
        self._set(entry_id, "released")

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [e.__dict__.copy() for e in self._entries]
