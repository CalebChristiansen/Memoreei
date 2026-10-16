"""Per-address rate limits, in memory: a restart forgives everyone."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable


class Limiter:
    """At most `count` events per `per` seconds for each key. allow() counts the
    call it answers; full() and note() let a caller count only some of them, such
    as refusals."""

    def __init__(self, count: int, per: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.count, self.per, self.clock = count, per, clock
        self.seen: dict[str, deque[float]] = defaultdict(deque)

    def full(self, key: str) -> bool:
        t = self.clock()
        q = self.seen.get(key)
        while q and q[0] <= t - self.per:
            q.popleft()
        return bool(q) and len(q) >= self.count

    def note(self, key: str) -> None:
        t = self.clock()
        self.seen[key].append(t)
        if len(self.seen) > 10_000:
            for k in [k for k, v in self.seen.items() if not v or v[-1] <= t - self.per]:
                del self.seen[k]

    def allow(self, key: str) -> bool:
        if self.full(key):
            return False
        self.note(key)
        return True
