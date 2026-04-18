use axum::extract::ws::CloseFrame;
use axum::http::StatusCode;
use axum::Router;
use axum::{
    extract::ws::{Message, WebSocket, WebSocketUpgrade},
    response::IntoResponse,
    routing::get,
};
use std::net::SocketAddr;
use tracing::{info, trace, warn};
use tracing_subscriber::EnvFilter;

fn listen_addr() -> SocketAddr {
    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(3000);
    SocketAddr::from(([0, 0, 0, 0], port))
}

async fn health() -> StatusCode {
    StatusCode::OK
}

fn init_tracing() {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| {
        // Default: quiet libraries; this crate logs INFO only for startup/diagnostics.
        // Per-message noise is TRACE (off unless RUST_LOG=trace or RUST_LOG=ws_rust=trace).
        EnvFilter::new("warn,ws_rust=info")
    });
    tracing_subscriber::fmt().with_env_filter(filter).init();
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    init_tracing();

    let app = Router::new()
        .route("/health", get(health))
        .route("/", get(websocket_handler));

    let addr = listen_addr();
    let listener = tokio::net::TcpListener::bind(addr).await?;

    info!(%addr, "listening (WebSocket at /)");
    axum::serve(listener, app).await?;

    Ok(())
}

// WebSocketUpgrade: Extractor for establishing WebSocket connections.
async fn websocket_handler(ws: WebSocketUpgrade) -> impl IntoResponse {
    // Finalize upgrading the connection and call the provided callback with the stream.
    ws.on_failed_upgrade(|error| warn!(%error, "websocket upgrade failed"))
    .read_buffer_size(1024)
    .write_buffer_size(1024)
    .on_upgrade(event_loop)
}

async fn event_loop(mut socket: WebSocket) {
    // Returns `None` if the stream has closed.
    while let Some(msg) = socket.recv().await {
        if let Ok(msg) = msg {
            match msg {
                Message::Text(utf8_bytes) => {
                    // Never log full payloads at INFO (volume + Railway log limits + privacy).
                    trace!(len = utf8_bytes.len(), "text frame");
                    let result = socket
                        .send(Message::Text(
                            format!("Echo back text: {}", utf8_bytes).into(),
                        ))
                        .await;
                    if let Err(error) = result {
                        warn!(%error, "send failed");
                        send_close_message(socket, 1011, &format!("Error occured: {}", error))
                            .await;
                        break;
                    }
                }
                Message::Binary(bytes) => {
                    trace!(len = bytes.len(), "binary frame");
                    let result = socket
                        .send(Message::Text(
                            format!("Received bytes of length: {}", bytes.len()).into(),
                        ))
                        .await;
                    if let Err(error) = result {
                        warn!(%error, "send failed");
                        send_close_message(socket, 1011, &format!("Error occured: {}", error))
                            .await;
                        break;
                    }
                }
                // Close, Ping, Pong will be handled automatically
                // Message::Close
                // After receiving a close frame, axum will automatically respond with a close frame if necessary (you do not have to deal with this yourself).
                // After sending a close frame, you may still read messages, but attempts to send another message will error.
                // Since no further messages will be received, you may either do nothing or explicitly drop the connection.
                _ => {}
            }
        } else {
            let error = msg.err().unwrap();
            warn!(?error, "recv failed");
            send_close_message(socket, 1011, &format!("Error occured: {}", error)).await;
            break;
        }
    }
}

// We MAY “uncleanly” close a WebSocket connection at any time by simply dropping the WebSocket, ie: Break out of the recv loop.
// However, you may also use the graceful closing protocol, in which
// peer A sends a close frame, and does not send any further messages;
// peer B responds with a close frame, and does not send any further messages;
// peer A processes the remaining messages sent by peer B, before finally
// both peers close the connection.
//
// Close Code: https://kapeli.com/cheat_sheets/WebSocket_Status_Codes.docset/Contents/Resources/Documents/index
async fn send_close_message(mut socket: WebSocket, code: u16, reason: &str) {
    _ = socket
        .send(Message::Close(Some(CloseFrame {
            code: code,
            reason: reason.into(),
        })))
        .await;
}