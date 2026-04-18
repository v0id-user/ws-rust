"""
Concurrent WebSocket load generator for the ws-rust echo server.

Example:
  uv run ws-stress -c 200 -d 30
  uv run ws-stress --url wss://example.up.railway.app/ -c 500 --interval 0
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import websockets


DEFAULT_URL = "wss://ws-rust-production.up.railway.app/"


class Progress:
    """Thread-safe (asyncio) counters for live progress lines."""

    __slots__ = ("lock", "msgs", "errors")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.msgs = 0
        self.errors = 0

    async def record_msg(self, n: int = 1) -> None:
        async with self.lock:
            self.msgs += n

    async def record_error(self) -> None:
        async with self.lock:
            self.errors += 1

    async def snapshot(self) -> tuple[int, int]:
        async with self.lock:
            return self.msgs, self.errors


async def progress_reporter(stop: asyncio.Event, progress: Progress, interval_s: float) -> None:
    """Print aggregate stats every interval_s until stop is set."""
    if interval_s <= 0:
        await stop.wait()
        return
    t0 = time.perf_counter()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            break
        except asyncio.TimeoutError:
            msgs, errs = await progress.snapshot()
            elapsed = time.perf_counter() - t0
            rate = msgs / elapsed if elapsed > 0 else 0.0
            print(
                f"  [{elapsed:6.1f}s] msgs={msgs}  ~{rate:.0f} msg/s  errors={errs}",
                flush=True,
            )


@dataclass
class ConnStats:
    sent: int = 0
    recv: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    last_error: str | None = None


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


async def run_one_connection(
    url: str,
    conn_id: int,
    duration_s: float,
    interval_s: float | None,
    payload_min: int,
    payload_max: int,
    ramp_delay_s: float,
    open_timeout_s: float,
    progress: Progress | None,
) -> ConnStats:
    stats = ConnStats()
    if ramp_delay_s > 0:
        await asyncio.sleep(ramp_delay_s)

    try:
        async with websockets.connect(
            url,
            open_timeout=open_timeout_s,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
        ) as ws:
            deadline = time.monotonic() + duration_s
            while time.monotonic() < deadline:
                n = random.randint(payload_min, payload_max)
                body = f"c{conn_id}:{stats.sent}:" + ("x" * max(0, n))
                t0 = time.perf_counter()
                await ws.send(body)
                _ = await ws.recv()
                rtt_ms = (time.perf_counter() - t0) * 1000.0
                stats.latencies_ms.append(rtt_ms)
                stats.sent += 1
                stats.recv += 1
                if progress is not None:
                    await progress.record_msg(1)
                if interval_s is not None and interval_s > 0:
                    await asyncio.sleep(interval_s)
    except Exception as e:  # noqa: BLE001 — surface any failure per connection
        stats.errors += 1
        stats.last_error = repr(e)
        if progress is not None:
            await progress.record_error()

    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Mass WebSocket stress against an echo server (asyncio + websockets).",
    )
    p.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"WebSocket URL (default: {DEFAULT_URL})",
    )
    p.add_argument(
        "-c",
        "--connections",
        type=int,
        default=100,
        help="Concurrent connections (default: 100)",
    )
    p.add_argument(
        "-d",
        "--duration",
        type=float,
        default=30.0,
        help="Seconds each connection keeps sending (default: 30)",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=None,
        metavar="SEC",
        help="Sleep between sends per connection (omit for full-speed flood)",
    )
    p.add_argument(
        "--payload-min",
        type=int,
        default=16,
        help="Min extra payload bytes per message (default: 16)",
    )
    p.add_argument(
        "--payload-max",
        type=int,
        default=256,
        help="Max extra payload bytes per message (default: 256)",
    )
    p.add_argument(
        "--ramp-up",
        type=float,
        default=0.0,
        help="Spread connection starts over this many seconds (default: 0)",
    )
    p.add_argument(
        "--open-timeout",
        type=float,
        default=30.0,
        help="TCP/TLS/WebSocket handshake timeout in seconds (default: 30)",
    )
    p.add_argument(
        "--progress-interval",
        type=float,
        default=1.0,
        metavar="SEC",
        help="Print live stats every SEC seconds (0 = disable). Default: 1",
    )
    return p.parse_args(argv)


async def run_all(args: argparse.Namespace) -> dict[str, Any]:
    if args.connections < 1:
        raise SystemExit("--connections must be >= 1")
    if args.duration <= 0:
        raise SystemExit("--duration must be > 0")
    if args.payload_min < 0 or args.payload_max < args.payload_min:
        raise SystemExit("invalid --payload-min / --payload-max")

    ramp = args.ramp_up
    delays: list[float]
    if ramp > 0 and args.connections > 1:
        step = ramp / (args.connections - 1)
        delays = [i * step for i in range(args.connections)]
    else:
        delays = [0.0] * args.connections

    progress = Progress()
    stop_progress = asyncio.Event()
    reporter = asyncio.create_task(
        progress_reporter(stop_progress, progress, args.progress_interval)
    )

    tasks = [
        asyncio.create_task(
            run_one_connection(
                args.url,
                conn_id=i,
                duration_s=args.duration,
                interval_s=args.interval,
                payload_min=args.payload_min,
                payload_max=args.payload_max,
                ramp_delay_s=delays[i],
                open_timeout_s=args.open_timeout,
                progress=progress,
            )
        )
        for i in range(args.connections)
    ]

    if args.progress_interval > 0:
        print("  [   0.0s] workers started", flush=True)

    wall0 = time.perf_counter()
    try:
        results = await asyncio.gather(*tasks)
    finally:
        stop_progress.set()
        await reporter

    wall_s = time.perf_counter() - wall0

    total_sent = sum(r.sent for r in results)
    total_recv = sum(r.recv for r in results)
    total_err = sum(r.errors for r in results)
    all_lat = [x for r in results for x in r.latencies_ms]
    all_lat.sort()

    out: dict[str, Any] = {
        "wall_s": wall_s,
        "connections": args.connections,
        "total_sent": total_sent,
        "total_recv": total_recv,
        "connection_errors": total_err,
        "msgs_per_s": total_sent / wall_s if wall_s > 0 else 0.0,
    }
    if all_lat:
        out["latency_ms_mean"] = statistics.fmean(all_lat)
        out["latency_ms_p50"] = percentile(all_lat, 50)
        out["latency_ms_p95"] = percentile(all_lat, 95)
        out["latency_ms_p99"] = percentile(all_lat, 99)
    else:
        out["latency_ms_mean"] = float("nan")
        out["latency_ms_p50"] = float("nan")
        out["latency_ms_p95"] = float("nan")
        out["latency_ms_p99"] = float("nan")

    sample_errors = [r.last_error for r in results if r.last_error][:5]
    out["sample_errors"] = sample_errors
    return out


def main() -> None:
    args = parse_args()
    print(f"Target: {args.url}")
    print(
        f"Plan: {args.connections} connections × {args.duration}s "
        f"(interval={args.interval!r}, ramp_up={args.ramp_up}s)",
        flush=True,
    )
    if args.progress_interval > 0:
        print(
            f"Live progress every {args.progress_interval}s "
            "(disable with --progress-interval 0)",
            flush=True,
        )
    print(flush=True)
    try:
        summary = asyncio.run(run_all(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130) from None

    print()
    print(f"Wall time:     {summary['wall_s']:.2f}s")
    print(f"Messages sent: {summary['total_sent']}  recv: {summary['total_recv']}")
    print(f"Aggregate:     {summary['msgs_per_s']:.1f} msg/s")
    print(
        "Latency (ms):  "
        f"mean={summary['latency_ms_mean']:.2f}  "
        f"p50={summary['latency_ms_p50']:.2f}  "
        f"p95={summary['latency_ms_p95']:.2f}  "
        f"p99={summary['latency_ms_p99']:.2f}",
    )
    print(f"Conn errors:   {summary['connection_errors']}")
    for err in summary["sample_errors"]:
        print(f"  sample: {err}")


if __name__ == "__main__":
    main()
