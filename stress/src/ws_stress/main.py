"""
Concurrent WebSocket load generator for the ws-rust echo server.

Example:
  uv run ws-stress -c 200 -d 30
  uv run ws-stress --url wss://example.up.railway.app/ -c 500 --interval 0
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import websockets


DEFAULT_URL = "wss://ws-rust-production.up.railway.app/"


def configure_line_buffered_streams() -> None:
    """Avoid block-buffered stdout in workers / pipes so lines show up immediately."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)  # py3.7+
        except (AttributeError, OSError, ValueError):
            pass


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


async def progress_reporter(
    stop: asyncio.Event,
    progress: Progress,
    interval_s: float,
    line_prefix: str,
) -> None:
    """Print aggregate stats every interval_s until stop is set (one asyncio loop / process)."""
    if interval_s <= 0:
        await stop.wait()
        return
    t0 = time.perf_counter()
    last_msgs = 0
    last_t = t0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            break
        except asyncio.TimeoutError:
            msgs, errs = await progress.snapshot()
            elapsed = time.perf_counter() - t0
            rate_avg = msgs / elapsed if elapsed > 0 else 0.0
            now = time.perf_counter()
            chunk = msgs - last_msgs
            dt = now - last_t
            rate_inst = chunk / dt if dt > 0 else 0.0
            last_msgs = msgs
            last_t = now
            # msgs = completed burst rounds (each may be burst>1 frames)
            print(
                f"{line_prefix}"
                f"{elapsed:7.1f}s  "
                f"msgs {msgs:>9}  "
                f"avg {rate_avg:>7.0f}/s  "
                f"inst {rate_inst:>7.0f}/s  "
                f"err {errs:>4}",
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
    use_binary: bool,
    burst: int,
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
            max_size=16 * 1024 * 1024,
        ) as ws:
            deadline = time.monotonic() + duration_s
            b = max(1, burst)
            while time.monotonic() < deadline:
                t0 = time.perf_counter()
                sent = 0
                while sent < b and time.monotonic() < deadline:
                    pad = random.randint(payload_min, payload_max)
                    if use_binary:
                        prefix = f"c{conn_id}:{stats.sent}:".encode()
                        body = prefix + (b"x" * max(0, pad))
                    else:
                        body = f"c{conn_id}:{stats.sent}:" + ("x" * max(0, pad))
                    await ws.send(body)
                    stats.sent += 1
                    sent += 1
                if sent == 0:
                    break
                for _ in range(sent):
                    _ = await ws.recv()
                    stats.recv += 1
                rtt_ms = (time.perf_counter() - t0) * 1000.0
                stats.latencies_ms.append(rtt_ms)
                if progress is not None:
                    await progress.record_msg(sent)
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
    p.add_argument(
        "-P",
        "--processes",
        type=int,
        default=1,
        help="OS processes, each running asyncio (splits -c across them). Default: 1",
    )
    p.add_argument(
        "--binary",
        action="store_true",
        help="Send binary frames instead of text (larger payloads are cheaper on the wire)",
    )
    p.add_argument(
        "--burst",
        type=int,
        default=1,
        help="Pipeline: send N frames then recv N (order preserved). Higher = more in-flight per RTT. Default: 1",
    )
    return p.parse_args(argv)


def _split_connections(total: int, parts: int) -> list[int]:
    if parts <= 0:
        return []
    base = total // parts
    rem = total % parts
    return [base + (1 if i < rem else 0) for i in range(parts)]


def _mp_worker(item: tuple[dict[str, Any], int, int, int]) -> dict[str, Any]:
    """Picklable entry point for ProcessPoolExecutor (each process: own asyncio + Progress lock)."""
    configure_line_buffered_streams()
    d, conn_id_base, conn_count, worker_id = item
    args = argparse.Namespace(**d)
    if args.progress_interval <= 0:
        print(
            f"[w{worker_id}] started ({conn_count} conns × {args.duration}s) — "
            "--progress-interval 0, so no periodic stats (stderr)",
            file=sys.stderr,
            flush=True,
        )
    return asyncio.run(
        run_all(
            args,
            conn_id_base=conn_id_base,
            connections=conn_count,
            progress_interval=args.progress_interval,
            worker_id=worker_id,
        )
    )


def merge_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    if not summaries:
        return {}
    wall_s = max(s["wall_s"] for s in summaries)
    total_sent = sum(s["total_sent"] for s in summaries)
    total_recv = sum(s["total_recv"] for s in summaries)
    total_err = sum(s["connection_errors"] for s in summaries)
    msgs_per_s = total_sent / wall_s if wall_s > 0 else 0.0
    conns = sum(s["connections"] for s in summaries)

    ok_msgs = sum(s["total_sent"] for s in summaries)
    if ok_msgs > 0:
        mean = sum(s["latency_ms_mean"] * s["total_sent"] for s in summaries) / ok_msgs
    else:
        mean = float("nan")

    def _mx(key: str) -> float:
        vals = [s[key] for s in summaries if not math.isnan(s[key])]
        return max(vals) if vals else float("nan")

    return {
        "wall_s": wall_s,
        "connections": conns,
        "total_sent": total_sent,
        "total_recv": total_recv,
        "connection_errors": total_err,
        "msgs_per_s": msgs_per_s,
        "latency_ms_mean": mean,
        "latency_ms_p50": _mx("latency_ms_p50"),
        "latency_ms_p95": _mx("latency_ms_p95"),
        "latency_ms_p99": _mx("latency_ms_p99"),
        "sample_errors": [e for s in summaries for e in s.get("sample_errors", [])][:5],
    }


async def run_all(
    args: argparse.Namespace,
    *,
    conn_id_base: int = 0,
    connections: int | None = None,
    progress_interval: float | None = None,
    worker_id: int | None = None,
) -> dict[str, Any]:
    if args.duration <= 0:
        raise SystemExit("--duration must be > 0")
    if args.payload_min < 0 or args.payload_max < args.payload_min:
        raise SystemExit("invalid --payload-min / --payload-max")

    conns = connections if connections is not None else args.connections
    if conns < 1:
        raise SystemExit("--connections must be >= 1")
    prog_iv = progress_interval if progress_interval is not None else args.progress_interval
    line_prefix = f"[w{worker_id}] " if worker_id is not None else ""

    ramp = args.ramp_up
    delays: list[float]
    if ramp > 0 and conns > 1:
        step = ramp / (conns - 1)
        delays = [i * step for i in range(conns)]
    else:
        delays = [0.0] * conns

    progress = Progress()
    stop_progress = asyncio.Event()
    reporter = asyncio.create_task(
        progress_reporter(stop_progress, progress, prog_iv, line_prefix)
    )

    tasks = [
        asyncio.create_task(
            run_one_connection(
                args.url,
                conn_id=i + conn_id_base,
                duration_s=args.duration,
                interval_s=args.interval,
                payload_min=args.payload_min,
                payload_max=args.payload_max,
                ramp_delay_s=delays[i],
                open_timeout_s=args.open_timeout,
                progress=progress,
                use_binary=args.binary,
                burst=args.burst,
            )
        )
        for i in range(conns)
    ]

    if prog_iv > 0:
        print(
            f"{line_prefix}"
            f"{0.0:7.1f}s  "
            f"start {conns:>3} conns  "
            f"(burst={args.burst} payload {args.payload_min}..{args.payload_max} {'bin' if args.binary else 'txt'})",
            flush=True,
        )

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
        "connections": conns,
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


def _dedupe_errors(samples: list[str], *, limit: int = 8) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for s in samples:
        key = s[:200] if len(s) > 200 else s
        counts[key] = counts.get(key, 0) + 1
    items = sorted(counts.items(), key=lambda x: -x[1])
    return items[:limit]


def print_final_summary(args: argparse.Namespace, summary: dict[str, Any]) -> None:
    """Structured summary to stdout."""
    sent = summary["total_sent"]
    recv = summary["total_recv"]
    gap = sent - recv
    wall = summary["wall_s"]
    mps = summary["msgs_per_s"]
    cerr = summary["connection_errors"]
    conns = summary["connections"]

    sep = "─" * 76
    print(sep, flush=True)
    print("  ws-stress — run finished", flush=True)
    print(sep, flush=True)
    print(f"  URL           {args.url}", flush=True)
    print(
        f"  Plan          {conns} conns × {args.duration}s  "
        f"P={args.processes}  burst={args.burst}  "
        f"payload {args.payload_min}..{args.payload_max}  "
        f"{'binary' if args.binary else 'text'}",
        flush=True,
    )
    print(f"  Wall time     {wall:.2f}s  (wall clock for this process / slowest worker in -P mode)", flush=True)
    print(flush=True)
    print("  Throughput", flush=True)
    print(f"    messages sent     {sent:>12,}", flush=True)
    print(f"    messages received {recv:>12,}", flush=True)
    if gap != 0:
        print(
            f"    gap (sent−recv)   {gap:>12,}  "
            "(partial burst or disconnect before all replies arrived)",
            flush=True,
        )
    else:
        print("    gap (sent−recv)            0", flush=True)
    print(f"    aggregate rate    {mps:>12,.1f} msg/s  (sent / wall time)", flush=True)
    print(flush=True)
    print("  Latency  (one sample per burst round-trip, ms)", flush=True)
    print(
        f"    mean {summary['latency_ms_mean']:8.2f}   "
        f"p50 {summary['latency_ms_p50']:8.2f}   "
        f"p95 {summary['latency_ms_p95']:8.2f}   "
        f"p99 {summary['latency_ms_p99']:8.2f}",
        flush=True,
    )
    print(flush=True)
    print("  Connection errors  (tasks that raised; one connection can fail once)", flush=True)
    print(f"    count           {cerr:>12}", flush=True)
    if sent > 0:
        print(f"    recv completion {100.0 * recv / sent:>11.2f}%  (recv/sent)", flush=True)
    if cerr > 0:
        print(
            "    status          some connections dropped — see samples below",
            flush=True,
        )
    else:
        print("    status          no connection-level exceptions", flush=True)
    samples = summary.get("sample_errors") or []
    if samples:
        print(flush=True)
        print("  Error samples  (deduplicated, up to 8)", flush=True)
        for text, cnt in _dedupe_errors(samples):
            print(f"    ×{cnt}  {text}", flush=True)
    print(sep, flush=True)


def main() -> None:
    configure_line_buffered_streams()
    args = parse_args()
    if args.connections < 1:
        raise SystemExit("--connections must be >= 1")
    if args.processes < 1:
        raise SystemExit("--processes must be >= 1")
    if args.burst < 1:
        raise SystemExit("--burst must be >= 1")

    hdr = "═" * 76
    print(hdr, flush=True)
    print("  ws-stress", flush=True)
    print(hdr, flush=True)
    print(f"  URL     {args.url}", flush=True)
    print(
        f"  Plan    {args.connections} conns × {args.duration}s  "
        f"processes={args.processes}  burst={args.burst}  "
        f"payload {args.payload_min}..{args.payload_max}  "
        f"{'binary' if args.binary else 'text'}  "
        f"interval={args.interval!r}",
        flush=True,
    )
    if args.processes > 1 and args.ramp_up > 0:
        print(
            "note: --ramp-up is ignored when using multiple processes",
            file=sys.stderr,
        )
    if args.progress_interval > 0:
        if args.processes == 1:
            print(
                f"Live progress every {args.progress_interval}s "
                "(disable with --progress-interval 0)",
                flush=True,
            )
        else:
            print(
                f"Each of {args.processes} processes logs independently with prefix "
                f"[w0]..[w{args.processes - 1}] every {args.progress_interval}s "
                "(no shared counter; use --progress-interval 0 to silence)",
                flush=True,
            )
    elif args.processes > 1:
        print(
            "note: --progress-interval 0 disables periodic [wN] lines. "
            "You will only see one stderr line per worker at start, then silence until the end. "
            "Use e.g. --progress-interval 5 for live stats.",
            file=sys.stderr,
            flush=True,
        )
    print(flush=True)
    try:
        if args.processes == 1:
            summary = asyncio.run(run_all(args))
        else:
            from concurrent.futures import ProcessPoolExecutor, as_completed

            print(
                "  Spawning worker processes (live lines prefixed [wN])…",
                flush=True,
            )
            counts = _split_connections(args.connections, args.processes)
            items: list[tuple[dict[str, Any], int, int, int]] = []
            base = 0
            d_base = dict(vars(args))
            d_base["ramp_up"] = 0.0
            for worker_id, n in enumerate(counts):
                if n <= 0:
                    continue
                items.append((d_base.copy(), base, n, worker_id))
                base += n
            summaries: list[dict[str, Any]] = []
            with ProcessPoolExecutor(max_workers=args.processes) as pool:
                futs = [pool.submit(_mp_worker, it) for it in items]
                for fut in as_completed(futs):
                    summaries.append(fut.result())
            summary = merge_summaries(summaries)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130) from None

    print_final_summary(args, summary)


if __name__ == "__main__":
    main()
