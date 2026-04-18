# ws-rust

**Axum WebSocket server + load-testing harness.** The service echoes text/binary frames on `/`, exposes **`GET /health`**, and runs **SHA-256 work per message** (round count is a **`const` in `src/main.rs`**) so you can stress **CPU**, not just I/O. Built while learning Rust; the Medium tutorial below is still the starting point.

**Suggested GitHub “About” line:** *Axum WebSocket echo server with per-message SHA-256 stress + Python/uv load generator.*

## What this repo is for

- **Production-shaped deploy**: Docker, Railway, `PORT`. **Tracing** defaults to **`error`** only and is **hard-capped at ERROR** (no `info!`/`warn!` on hot paths) so Railway log rate limits stay happy.
- **Load generation**: [`stress/`](stress/) uses **uv** + asyncio + **websockets**; use **many connections**, **`--burst`** (pipeline sends per RTT), **`-P`** processes, **smaller payloads** for higher **msg/s**, and **no `--interval`**. **Huge payloads + CPU hashing** lowers msg/s on purpose.
- **CPU stress (server)**: **`STRESS_HASH_ROUNDS`** in [`src/main.rs`](src/main.rs) defaults to **`2000`** (chained SHA-256 per message). Set to **`0`** for max echo **msg/s** (no hashing).

## Learning notes

This started from:

**[Rust WebSocket with Axum for realtime communications](https://medium.com/@itsuki.enjoy/rust-websocket-with-axum-for-realtime-communications-49a93468268f)** (Medium, Itsuki)

## Prerequisites

- [Rust](https://www.rust-lang.org/tools/install) (stable), `cargo`
- For the stress client: [uv](https://docs.astral.sh/uv/) (Python 3.11+)

## Run (server)

```bash
cargo run
```

By default there is **no** startup log line. **`RUST_LOG`** defaults to **`error`**; the subscriber also uses **`with_max_level(ERROR)`**, so even a broad env filter cannot emit INFO/WARN/DEBUG/TRACE to stdout.

### CPU work (hardcoded)

Edit **`const STRESS_HASH_ROUNDS: usize`** at the top of [`src/main.rs`](src/main.rs). **`0`** = echo only (best msg/s). **`2000`** (default) = that many chained SHA-256 rounds per message (CPU-heavy, lower msg/s).

## Try it (WebSocket)

Connect to `ws://127.0.0.1:3000/` (or `ws://localhost:3000/`).

- **[websocat](https://github.com/vi/websocat):** `websocat ws://127.0.0.1:3000/`
- **Browser console:**

  ```js
  const ws = new WebSocket("ws://127.0.0.1:3000/");
  ws.onmessage = (e) => console.log(e.data);
  ws.onopen = () => ws.send("hello");
  ```

## Load test (Python + uv)

[`stress/`](stress/) drives **`wss://`** (or local **`ws://`**) with concurrent connections, optional **binary** frames, and **`-P` / `--processes`** to split connections across multiple OS processes (useful when one asyncio loop is not enough client-side).

```bash
cd stress
uv sync
# Higher msg/s (set STRESS_HASH_ROUNDS=0 in main.rs first; small frames; burst)
uv run ws-stress \
  --url 'wss://YOUR-SERVICE.up.railway.app/' \
  -c 500 -d 120 -P 8 \
  --payload-min 512 --payload-max 8192 \
  --binary --burst 16 \
  --progress-interval 10
# Default server build uses STRESS_HASH_ROUNDS=2000 (CPU stress). Use large --payload-max to stress bandwidth + CPU.
```

Defaults target **`wss://ws-rust-production.up.railway.app/`**; override with **`--url`**. Omit **`--interval`** for full-speed loops. With **`-P` > 1**, each OS process prints its own stats with a **`[wN]`** prefix (no shared counter). **`--progress-interval 0`** turns off those lines entirely—use a value like **`5`** if you want periodic output without spam. Be considerate of shared infrastructure and Railway limits.

## Stack

| Crate | Role |
|--------|------|
| [axum](https://crates.io/crates/axum) (`ws`) | HTTP + WebSocket |
| [tokio](https://crates.io/crates/tokio) | Async runtime |
| [sha2](https://crates.io/crates/sha2) | Per-message SHA-256 (CPU stress; rounds = `const` in `main.rs`) |
| [tracing](https://crates.io/crates/tracing) / [tracing-subscriber](https://crates.io/crates/tracing-subscriber) | Default `RUST_LOG=error`; `with_max_level(ERROR)` hard cap |
| [anyhow](https://crates.io/crates/anyhow) | Errors in `main` |

`serde` is in `Cargo.toml` for typical future use.

## Deploy on Railway

This repo includes a [`Dockerfile`](Dockerfile) and [`railway.toml`](railway.toml) (Docker build, **`/health`** check).

1. Push to GitHub and connect the repo in [Railway](https://railway.com).
2. Railway sets **`PORT`**; the server listens on **`0.0.0.0`**.
3. You usually **do not** need **`RUST_LOG`** in Railway; the binary already caps output at **ERROR**.
4. CPU stress level is whatever **`STRESS_HASH_ROUNDS`** is set to in source before you deploy (see [`src/main.rs`](src/main.rs)).
5. Use the public URL with **`wss://`** for browsers. **`ws://`** is for localhost only.

Local port override: `PORT=8080 cargo run`.
