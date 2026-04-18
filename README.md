# ws-rust

**Axum WebSocket server + load-testing harness.** The service echoes text/binary frames on `/`, exposes **`GET /health`**, and runs **SHA-256 work per message** (round count is a **`const` in `src/main.rs`**) so you can stress **CPU**, not just I/O. Built while learning Rust; the Medium tutorial below is still the starting point.

**Suggested GitHub “About” line:** *Axum WebSocket echo server with per-message SHA-256 stress + Python/uv load generator.*

## What this repo is for

- **Production-shaped deploy**: Docker, Railway, `PORT`, `RUST_LOG`, no per-frame log spam.
- **Load generation**: [`stress/`](stress/) uses **uv** + asyncio + **websockets**; use **many connections**, **large payloads**, **multiple OS processes**, and **no `--interval`** to push throughput.
- **CPU stress (server)**: **`STRESS_HASH_ROUNDS`** in [`src/main.rs`](src/main.rs) (hardcoded for this dummy repo). Each message is hashed that many times (chained SHA-256) on Tokio’s **blocking pool**. Set the **`const`** to **`0`** if you want echo-only.

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

You should see an **INFO** line with the bound address (default filter: `warn,ws_rust=info`). Override with **`RUST_LOG`** (e.g. `RUST_LOG=warn` on Railway).

### CPU work (hardcoded)

Edit **`const STRESS_HASH_ROUNDS: usize`** at the top of [`src/main.rs`](src/main.rs). **`0`** = no hashing (echo only). Non-zero = that many chained SHA-256 rounds per message before echoing (can dominate latency).

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
# Throughput: no --interval, bigger payloads, more connections
uv run ws-stress -c 200 -d 60 --payload-max 65536
# Multiprocess client (splits -c across processes)
uv run ws-stress -c 400 -d 60 -P 4
# Binary frames + large payloads
uv run ws-stress -c 100 -d 30 --binary --payload-min 4096 --payload-max 262144
```

Defaults target **`wss://ws-rust-production.up.railway.app/`**; override with **`--url`**. Omit **`--interval`** for full-speed loops. **`--progress-interval 0`** silences live lines. Be considerate of shared infrastructure and Railway limits.

## Stack

| Crate | Role |
|--------|------|
| [axum](https://crates.io/crates/axum) (`ws`) | HTTP + WebSocket |
| [tokio](https://crates.io/crates/tokio) | Async runtime |
| [sha2](https://crates.io/crates/sha2) | Per-message SHA-256 (CPU stress; rounds = `const` in `main.rs`) |
| [tracing](https://crates.io/crates/tracing) / [tracing-subscriber](https://crates.io/crates/tracing-subscriber) | Logging (`RUST_LOG`) |
| [anyhow](https://crates.io/crates/anyhow) | Errors in `main` |

`serde` is in `Cargo.toml` for typical future use.

## Deploy on Railway

This repo includes a [`Dockerfile`](Dockerfile) and [`railway.toml`](railway.toml) (Docker build, **`/health`** check).

1. Push to GitHub and connect the repo in [Railway](https://railway.com).
2. Railway sets **`PORT`**; the server listens on **`0.0.0.0`**.
3. Optional: **`RUST_LOG=warn`** or **`error`** if you want minimal logs.
4. CPU stress level is whatever **`STRESS_HASH_ROUNDS`** is set to in source before you deploy (see [`src/main.rs`](src/main.rs)).
5. Use the public URL with **`wss://`** for browsers. **`ws://`** is for localhost only.

Local port override: `PORT=8080 cargo run`.
