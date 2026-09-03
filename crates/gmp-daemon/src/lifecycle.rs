//! Bringing the daemon up and putting it away again.
//!
//! Only the parts that exist yet. The poll tick, the observer callbacks and
//! the payload apply/revert are the next block; what is here is the service
//! itself - claim the name, publish the object, wait to be told to stop.
//!
//! The shutdown path is deliberately its own thing rather than a `Drop`. The
//! Python daemon reverts what it applied on the way out, and that work has to
//! happen before the bus name goes away, in an order this can state plainly.

use std::sync::Arc;

use tokio::sync::Mutex;
use zbus::connection;

use crate::api::{Api, BUS_NAME, OBJECT_PATH};
use crate::state::DaemonState;

/// Serve the frozen interface on the session bus until cancelled.
///
/// Claiming the name is what makes this daemon the one the GUI talks to, so it
/// is deliberately NOT `replace_existing`: two daemons applying tweaks to the
/// same machine is the failure the Python daemon's own single-instance check
/// exists to prevent, and losing the race must look like losing, not like
/// silently taking over from a daemon that still holds applied state.
pub async fn serve(state: Arc<Mutex<DaemonState>>) -> anyhow::Result<connection::Connection> {
    let conn = connection::Builder::session()?
        .name(BUS_NAME)?
        .serve_at(OBJECT_PATH, Api::new(state))?
        .build()
        .await?;
    tracing::info!("serving {BUS_NAME} at {OBJECT_PATH}");
    Ok(conn)
}
