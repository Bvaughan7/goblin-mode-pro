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
mod fans;
mod manager;
mod nvidia;
mod polkit;
mod power;
mod renice;
mod revert;
mod sys;
mod sysctl;
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
    //
    // Deliberately BEFORE the root check, and deliberately the only thing that
    // is: it reads no privileged state and CI has to be able to run it. Every
    // other mode needs root.
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|arg| arg == "--introspect") {
        print!("{}", manager::introspection_xml());
        return Ok(());
    }

    // Unknown arguments are refused rather than ignored, and refused BEFORE
    // the root check so a typo is distinguishable from a permission problem.
    // The Python helper only ever compares argv[1] against "--revert" and
    // starts serving the bus for anything else, which means a mistyped flag
    // silently launches a privileged service. Not worth reproducing.
    if let Some(unknown) = args.iter().find(|arg| arg.as_str() != "--revert") {
        eprintln!("goblin-helper: unknown argument {unknown}");
        eprintln!("usage: gmp-helper [--revert | --introspect]");
        std::process::exit(2);
    }

    // Matches the Python helper, which refuses the same way. Without it the
    // failure further down is a confusing permission error from the bus
    // instead of a sentence saying what is wrong.
    if !rustix::process::geteuid().is_root() {
        eprintln!("goblin-helper must run as root");
        std::process::exit(1);
    }

    let roots = sys::Roots::system();

    // The unit's ExecStopPost runs this. It is what puts the machine back when
    // the service stops, so it has to exist in BOTH implementations or
    // swapping the symlink silently stops reverting on shutdown - the sort of
    // break nobody notices until a governor survives a reboot cycle.
    if args.iter().any(|arg| arg == "--revert") {
        let reverted = revert::revert_all(&roots)
            .await
            .map_err(|err| anyhow::anyhow!("revert failed: {err:?}"))?;
        std::process::exit(i32::from(!reverted));
    }

    // BEFORE anything is served. A fan left under manual control by an
    // instance that died is the one state this helper must never sit in, and
    // a caller must not be able to race the recovery.
    fans::recover_after_restart(&roots);

    let _conn = zbus::connection::Builder::system()
        .context("no system bus - the helper is not meant to run on a session bus")?
        .name(manager::BUS_NAME)
        .context("invalid bus name")?
        .serve_at(manager::OBJECT_PATH, manager::Manager::new(roots))
        .context("invalid object path")?
        .build()
        .await
        .with_context(|| {
            format!(
                "could not claim {}. Either the bus policy does not allow this \
                 user to own it (it is root-only, so run under the unit rather \
                 than by hand), or another helper already holds it",
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
