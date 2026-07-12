"""In-process performance diagnostics for the gateway, queryable via the API.

Built to make the production event-loop stalls diagnosable: gui.log shows
bursts of "event loop stalled Ns (GIL pressure suspected)" (observed 8-63s),
but nothing recorded WHICH RPC handlers, queue delays, or session.create /
session.resume stages were burning the time. server.py records every RPC
(run time + pool queue time), the web_server heartbeat feeds loop-lag drift,
and the create/resume/agent-build paths mark named stages. One JSON snapshot
of all of it is exposed as the ``diagnostics.perf`` JSON-RPC method and
``GET /api/diagnostics/perf`` REST route.

Design constraints:
- Thread-safe: records arrive from the RPC pool, the deferred agent-build
  threads, and the asyncio heartbeat callback concurrently.
- O(1)-ish per record with small fixed rings, so instrumenting hot paths
  costs microseconds and memory stays bounded for the process lifetime.
- ``snapshot()`` returns only JSON-safe scalars/lists/dicts.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

# Ring sizes: large enough for meaningful p50/p95 over recent traffic, small
# enough that hundreds of distinct methods/stages stay at a few MB total.
_SAMPLE_RING = 128
_SLOWEST_RING = 30
_LAG_RING = 50


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(
        ts if ts is not None else time.time(), tz=timezone.utc
    ).isoformat(timespec="milliseconds")


def _pctl(sorted_vals: list, fraction: float) -> float:
    """Nearest-rank percentile over an already-sorted list (0.0 if empty)."""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(fraction * len(sorted_vals)))
    return float(sorted_vals[idx])


class _Series:
    """count / error count / all-time max plus a ring of recent samples for
    p50/p95. Percentiles are computed lazily at snapshot() time so record()
    stays cheap on the hot path."""

    __slots__ = ("count", "errors", "max_ms", "samples")

    def __init__(self) -> None:
        self.count = 0
        self.errors = 0
        self.max_ms = 0.0
        self.samples: deque = deque(maxlen=_SAMPLE_RING)

    def add(self, ms: float, ok: bool = True) -> None:
        self.count += 1
        if not ok:
            self.errors += 1
        if ms > self.max_ms:
            self.max_ms = ms
        self.samples.append(ms)

    def stats(self) -> dict:
        vals = sorted(self.samples)
        return {
            "count": self.count,
            "errors": self.errors,
            "p50_ms": round(_pctl(vals, 0.50), 2),
            "p95_ms": round(_pctl(vals, 0.95), 2),
            "max_ms": round(self.max_ms, 2),
        }


class Diagnostics:
    """Process-wide perf collector (use the module-level ``diagnostics``
    singleton; a fresh instance is only for tests)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        # Per-RPC-method run-time series.
        self._rpc: dict[str, _Series] = {}
        # The N slowest RPC calls seen since process start (not a ring of the
        # latest N — the point is to keep the worst offenders visible even
        # after traffic moves on).
        self._slowest: list[dict] = []
        self._slowest_floor = 0.0  # min ms currently held, for a cheap gate
        # Event-loop lag (fed by the web_server 2s heartbeat watchdog).
        self._lag_last_ms = 0.0
        self._lag_max_ms = 0.0
        self._lag_ticks = 0
        self._lag_over_1s = 0
        self._lag_over_5s = 0
        self._lag_recent: deque = deque(maxlen=_LAG_RING)
        # Named stage timings, keyed (flow, stage) — e.g.
        # ("session.resume", "db_read").
        self._stages: dict[tuple[str, str], _Series] = {}

    # ── recording ────────────────────────────────────────────────────

    def record_rpc(
        self, method: str, ms: float, queue_ms: float = 0.0, ok: bool = True
    ) -> None:
        """Record one RPC dispatch: handler run time plus (for pool-routed
        long handlers) the time the request sat queued behind other work."""
        with self._lock:
            series = self._rpc.get(method)
            if series is None:
                series = self._rpc[method] = _Series()
            series.add(ms, ok)
            if len(self._slowest) < _SLOWEST_RING or ms > self._slowest_floor:
                entry = {
                    "method": method,
                    "ms": round(ms, 2),
                    "queue_ms": round(queue_ms, 2),
                    "ts": _iso(),
                }
                if len(self._slowest) < _SLOWEST_RING:
                    self._slowest.append(entry)
                else:
                    # Replace the current minimum (list is only 30 long).
                    idx = min(
                        range(len(self._slowest)),
                        key=lambda i: self._slowest[i]["ms"],
                    )
                    self._slowest[idx] = entry
                self._slowest_floor = min(e["ms"] for e in self._slowest)

    def record_loop_lag(self, drift_ms: float) -> None:
        """Record one heartbeat tick's drift — how late the asyncio loop fired
        a 2s timer. Sustained large drift is the 'GIL pressure' stall."""
        drift_ms = max(0.0, float(drift_ms))
        with self._lock:
            self._lag_ticks += 1
            self._lag_last_ms = drift_ms
            if drift_ms > self._lag_max_ms:
                self._lag_max_ms = drift_ms
            if drift_ms > 1000.0:
                self._lag_over_1s += 1
            if drift_ms > 5000.0:
                self._lag_over_5s += 1
            # Only keep interesting ticks in the ring: an idle loop drifts
            # ~0ms and would otherwise flush real stalls out of the window.
            if drift_ms >= 50.0:
                self._lag_recent.append(
                    {"drift_ms": round(drift_ms, 1), "ts": _iso()}
                )

    def record_stage(self, flow: str, stage: str, ms: float) -> None:
        """Record a named stage of a known flow ('session.create',
        'session.resume', 'agent.build', ...)."""
        with self._lock:
            key = (flow, stage)
            series = self._stages.get(key)
            if series is None:
                series = self._stages[key] = _Series()
            series.add(ms)

    # ── reporting ────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """One JSON-safe dict with everything (RPC stats, slowest calls,
        loop lag, stage timings, uptime, pid)."""
        with self._lock:
            rpc = {name: s.stats() for name, s in self._rpc.items()}
            slowest = sorted(
                (dict(e) for e in self._slowest),
                key=lambda e: e["ms"],
                reverse=True,
            )
            stages: dict[str, dict] = {}
            for (flow, stage), s in self._stages.items():
                stages.setdefault(flow, {})[stage] = s.stats()
            event_loop = {
                "last_ms": round(self._lag_last_ms, 1),
                "max_ms": round(self._lag_max_ms, 1),
                "ticks": self._lag_ticks,
                "stalls_over_1s": self._lag_over_1s,
                "stalls_over_5s": self._lag_over_5s,
                "recent": list(self._lag_recent),
            }
        return {
            "pid": os.getpid(),
            "started_at": _iso(self._started_at),
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "rpc": rpc,
            "slowest_calls": slowest,
            "event_loop": event_loop,
            "stages": stages,
        }


# Process-wide singleton + thin module-level aliases so call sites can do
# ``from tui_gateway.diagnostics import record_loop_lag`` without holding a
# reference to the object.
diagnostics = Diagnostics()

record_rpc = diagnostics.record_rpc
record_loop_lag = diagnostics.record_loop_lag
record_stage = diagnostics.record_stage
snapshot = diagnostics.snapshot
