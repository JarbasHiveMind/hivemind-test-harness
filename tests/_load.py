"""Helpers for sustained-load scenarios against a real transport.

The in-process simulator answers "is the protocol correct". These helpers
answer "what does the node do when N satellites arrive at once", which needs a
real listener, real sockets and a synchronised start.
"""
import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class LoadResult:
    """Client-observed latencies for one burst, in milliseconds."""
    samples: List[float] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    wall_ms: float = 0.0

    @property
    def ok(self) -> int:
        return len(self.samples)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.samples) if self.samples else float("nan")

    def pct(self, p: float) -> float:
        """The p-th percentile, nearest-rank."""
        if not self.samples:
            return float("nan")
        ordered = sorted(self.samples)
        idx = min(int(len(ordered) * p / 100), len(ordered) - 1)
        return ordered[idx]

    @property
    def p50(self) -> float:
        return self.pct(50)

    @property
    def p95(self) -> float:
        return self.pct(95)

    def summary(self) -> str:
        return (f"n={self.ok} errors={len(self.errors)} "
                f"mean={self.mean:.1f}ms p50={self.p50:.1f}ms "
                f"p95={self.p95:.1f}ms wall={self.wall_ms:.1f}ms")


def burst(action: Callable[[int], None], n: int,
          timeout: float = 120.0,
          on_cleanup: Optional[Callable[[], None]] = None) -> LoadResult:
    """Run ``action(i)`` on ``n`` threads released simultaneously.

    Threads are started and parked on a gate first, so the measurement covers
    contention on the node rather than the cost of spawning threads. Each
    thread times its own ``action`` call.
    """
    result = LoadResult()
    lock = threading.Lock()
    gate = threading.Event()

    def run(i: int) -> None:
        gate.wait()
        started = time.monotonic()
        try:
            action(i)
        except Exception as e:  # noqa: BLE001 - the point is to record it
            with lock:
                result.errors.append(f"{type(e).__name__}: {e}"[:200])
            return
        elapsed = (time.monotonic() - started) * 1000
        with lock:
            result.samples.append(elapsed)

    threads = [threading.Thread(target=run, args=(i,), daemon=True)
               for i in range(n)]
    for t in threads:
        t.start()
    # let every thread reach the gate before the clock starts
    time.sleep(0.5)
    wall_started = time.monotonic()
    gate.set()
    for t in threads:
        t.join(timeout=timeout)
    result.wall_ms = (time.monotonic() - wall_started) * 1000

    if on_cleanup is not None:
        on_cleanup()
    return result
