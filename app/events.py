"""In-process pub/sub used to stream agent and browser activity to the UI."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Event:
    type: str
    message: str
    data: dict[str, Any] | None = None
    run_id: str | None = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "message": self.message,
            "data": self.data,
            "run_id": self.run_id,
            "ts": self.ts,
        }

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False, default=str)}\n\n"


class EventBus:
    """Fan-out to every connected dashboard tab, with slow-client protection."""

    def __init__(self, max_queue: int = 500) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._max_queue = max_queue
        self._sequence = 0

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    def publish(
        self,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Event:
        self._sequence += 1
        event = Event(type=kind, message=message, data=data, run_id=run_id)
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue
        return event

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
