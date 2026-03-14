"""
MessageRecorder — attaches to a node and captures every inbound/outbound HiveMessage.
Provides blocking wait_for() and assertion helpers for tests.
"""
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class RecordedMessage:
    direction: str        # "in" | "out" | "bus_inject"
    msg_type: str         # HiveMessageType value or OVOS message type
    payload: Any
    peer: str
    timestamp: float = field(default_factory=time.monotonic)

    def __repr__(self):
        return f"RecordedMessage({self.direction}, {self.msg_type!r}, peer={self.peer!r})"


class MessageRecorder:
    def __init__(self, name: str):
        self.name = name
        self.records: List[RecordedMessage] = []
        self._waiters: Dict[str, List[threading.Event]] = defaultdict(list)
        self._lock = threading.Lock()

    def record(self, direction: str, msg_type: str, payload: Any, peer: str):
        entry = RecordedMessage(direction=direction, msg_type=msg_type,
                                payload=payload, peer=peer)
        with self._lock:
            self.records.append(entry)
            for ev in self._waiters.get(msg_type, []):
                ev.set()

    def wait_for(self,
                 msg_type: str,
                 direction: Optional[str] = None,
                 timeout: float = 5.0) -> Optional[RecordedMessage]:
        """Block until a matching record appears; return it or None on timeout."""
        existing = self._find(msg_type, direction)
        if existing:
            return existing

        ev = threading.Event()
        with self._lock:
            self._waiters[msg_type].append(ev)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ev.wait(timeout=remaining)
            result = self._find(msg_type, direction)
            if result:
                with self._lock:
                    if ev in self._waiters[msg_type]:
                        self._waiters[msg_type].remove(ev)
                return result
            if not ev.is_set():
                break
            ev.clear()

        with self._lock:
            if ev in self._waiters.get(msg_type, []):
                self._waiters[msg_type].remove(ev)
        return None

    def assert_received(self, msg_type: str,
                        count: int = 1,
                        direction: Optional[str] = None):
        matches = self._find_all(msg_type, direction)
        assert len(matches) == count, (
            f"[{self.name}] Expected {count}x '{msg_type}' "
            f"(direction={direction!r}), got {len(matches)}.\n"
            f"All records: {self.records}"
        )

    def assert_not_received(self, msg_type: str, direction: Optional[str] = None):
        matches = self._find_all(msg_type, direction)
        assert not matches, (
            f"[{self.name}] Expected '{msg_type}' NOT received "
            f"(direction={direction!r}), but got {len(matches)}.\n"
            f"Records: {matches}"
        )

    def received(self, msg_type: str,
                 direction: Optional[str] = None) -> List[RecordedMessage]:
        return self._find_all(msg_type, direction)

    def clear(self):
        with self._lock:
            self.records.clear()

    # --- internal ---

    def _find(self, msg_type: str, direction: Optional[str]) -> Optional[RecordedMessage]:
        results = self._find_all(msg_type, direction)
        return results[-1] if results else None

    def _find_all(self, msg_type: str, direction: Optional[str]) -> List[RecordedMessage]:
        with self._lock:
            return [
                r for r in self.records
                if r.msg_type == msg_type
                and (direction is None or r.direction == direction)
            ]
