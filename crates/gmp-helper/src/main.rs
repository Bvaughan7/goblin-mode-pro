//! The privileged system helper, in Rust.
//!
//! It serves `docs/dbus-interface-v1.xml` on the system bus under the same
//! name the Python helper uses. Only one of the two may be running: they claim
//! the same bus name, and whichever gets there first wins. That is deliberate:
//! the deployment plan installs one unit and points a symlink at whichever
//! implementation is wanted, so rolling back is swapping a link rather than
//! reconfiguring D-Bus.
//!
//! Nothing here touches hardware yet. See `manager`.

mod cpu;
mod error;
mod manager;
mod polkit;
mod power;
mod sys;
mod undervolt;
// The snapshot is WRITTEN from here already; it is not read back until
// RevertAll is ported. Getting the format right first is deliberate - it is
// the compatibility surface between the two helpers, and a Python RevertAll
// has to be able to restore from a file this binary wrote.
mod state;

use anyhow::Context;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    // `--introspect` prints the served interface and exits without touching
    // the bus, so the freeze test can run anywhere - in CI, in a container, on
    // a machine where the real helper is already running and holding the name.
    if std::env::args().skip(1).any(|arg| arg == "--introspect") {
        print!("{}", manager::introspection_xml());
        return Ok(());
    }

    let _conn = zbus::connection::Builder::system()
        .context("no system bus - the helper is not meant to run on a session bus")?
        .name(manager::BUS_NAME)
        .context("invalid bus name")?
        .serve_at(
            manager::OBJECT_PATH,
            manager::Manager::new(sys::Roots::system()),
        )
        .context("invalid object path")?
        .build()
        .await
        .with_context(|| {
            format!(
                "could not claim {}; is the Python helper still running?",
                manager::BUS_NAME
            )
        })?;

    tracing::info!("serving {} at {}", manager::INTERFACE, manager::OBJECT_PATH);

    // systemd stops the unit with SIGTERM; without handling it the process is
    // killed rather than exiting, and the bus name is released a moment later
    // than it should be.
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .context("could not install the SIGTERM handler")?;
    tokio::select! {
        _ = term.recv() => tracing::info!("SIGTERM - shutting down"),
        _ = tokio::signal::ctrl_c() => tracing::info!("SIGINT - shutting down"),
    }
    Ok(())
}
