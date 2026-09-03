//! `gmp-daemon` - see the crate documentation in `lib.rs`.

use std::sync::Arc;

use gmp_daemon::{api, lifecycle, state};
use tokio::sync::Mutex;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();

    // `--introspect` prints the served interface and exits without touching a
    // bus, so the freeze check runs in CI and in a container where there is no
    // session bus to connect to - and on a machine where the Python daemon
    // already holds the bus name.
    if args.iter().any(|arg| arg == "--introspect") {
        print!("{}", api::introspection_xml());
        return Ok(());
    }
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        eprintln!("usage: gmp-daemon [--introspect]");
        return Ok(());
    }
    if let Some(unknown) = args.first() {
        eprintln!("gmp-daemon: unknown argument {unknown:?}");
        std::process::exit(2);
    }

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let state = Arc::new(Mutex::new(state::DaemonState::default()));
    let _conn = lifecycle::serve(state).await?;

    tracing::warn!(
        "this daemon serves the frozen interface but no method is ported yet - \
         run the Python daemon to actually do anything"
    );
    tokio::signal::ctrl_c().await?;
    tracing::info!("stopping");
    Ok(())
}
