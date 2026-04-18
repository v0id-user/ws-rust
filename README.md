# ws-rust

A small [Axum](https://github.com/tokio-rs/axum) WebSocket server written while learning Rust. It binds to **all interfaces** on the port from the `PORT` environment variable (default **3000**), upgrades HTTP requests at `/` to WebSockets, and echoes back text and binary messages. Logging uses **`tracing`**: startup is **INFO**; per-frame logs are **TRACE** only (so production is not flooded). **`GET /health`** returns `200 OK` for load balancers and platforms like Railway.

## Learning notes

This repo follows the walkthrough:

**[Rust WebSocket with Axum for realtime communications](https://medium.com/@itsuki.enjoy/rust-websocket-with-axum-for-realtime-communications-49a93468268f)** (Medium, Itsuki)

## Prerequisites

- [Rust](https://www.rust-lang.org/tools/install) (stable), `cargo`

## Run

```bash
cargo run
```

You should see an **INFO** line with the bound address (default filter: `warn,ws_rust=info`). Override with **`RUST_LOG`** (for example `RUST_LOG=warn` on Railway to log almost nothing beyond library warnings).

## Try it

Connect a WebSocket client to `ws://127.0.0.1:3000/` (or `ws://localhost:3000/`).

Examples:

- **[websocat](https://github.com/vi/websocat):** `websocat ws://127.0.0.1:3000/` then type lines; the server echoes them.
- **Browser devtools:** open the console on any page and run:

  ```js
  const ws = new WebSocket("ws://127.0.0.1:3000/");
  ws.onmessage = (e) => console.log(e.data);
  ws.onopen = () => ws.send("hello");
  ```

## Load test (Python + uv)

The [`stress/`](stress/) directory is a small **[uv](https://docs.astral.sh/uv/)** project that opens many concurrent **`wss://`** connections and sends echo traffic (asyncio + **websockets**).

```bash
cd stress
uv sync
uv run ws-stress -c 200 -d 60
```

Defaults target **`wss://ws-rust-production.up.railway.app/`**; override with **`--url`**. Omit **`--interval`** for maximum send rate, or set **`--ramp-up`** to stagger connection starts. Live stats print every second (change with **`--progress-interval`**, or **`--progress-interval 0`** to turn off). Be considerate of shared infrastructure and your Railway limits.

## Stack

| Crate | Role |
|--------|------|
| [axum](https://crates.io/crates/axum) (`ws`) | HTTP router and WebSocket upgrade |
| [tokio](https://crates.io/crates/tokio) | Async runtime |
| [anyhow](https://crates.io/crates/anyhow) | Error handling in `main` |

`serde` is listed in `Cargo.toml` for typical future use. **`tracing`** / **`tracing-subscriber`** drive server logs (`RUST_LOG`, default `warn,ws_rust=info`).

## Deploy on Railway

This repo includes a [`Dockerfile`](Dockerfile) and [`railway.toml`](railway.toml) so Railway uses Docker and checks **`/health`** after deploy.

1. Push the project to GitHub (or connect a repo in [Railway](https://railway.com)).
2. **New project → Deploy from GitHub** (or CLI) and select this repository.
3. Railway sets **`PORT`** automatically; do not hardcode it. The server reads `PORT` and listens on `0.0.0.0`.
4. Optional: set **`RUST_LOG=warn`** (or **`error`**) in Railway if you still hit platform log rate limits — the WebSocket hot path no longer logs every frame.
5. After deploy, use the public **URL** Railway shows. For browsers and clients, use **`wss://`** with that host (for example `wss://example.up.railway.app/`). Use **`ws://`** only on localhost.

Optional: set **`PORT`** locally to match another port (`PORT=8080 cargo run`).
