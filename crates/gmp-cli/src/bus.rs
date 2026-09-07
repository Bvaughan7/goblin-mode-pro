//! Asking the daemon, over the session bus.
//!
//! A thin client and nothing more. Every reply on this interface is a JSON
//! STRING rather than a structured type - deliberately, so that adding a field
//! to a reply is not a change to the frozen contract - which means the parsing
//! belongs here and the rendering belongs beside the Python it has to match.

use anyhow::{Context, Result};
use serde_json::Value;

pub const BUS_NAME: &str = "com.goblinmode.Pro.Daemon";
pub const OBJECT_PATH: &str = "/com/goblinmode/Pro/Daemon";
pub const INTERFACE: &str = "com.goblinmode.Pro.Daemon";

/// A connection to whichever daemon is serving the interface.
pub struct Daemon {
    proxy: zbus::Proxy<'static>,
}

impl Daemon {
    /// Connect, without starting anything.
    ///
    /// No auto-start: this is a client somebody typed, and a CLI that silently
    /// launches a background service because it was asked for a status line is
    /// doing something the person did not ask for. A daemon that is not
    /// running is a thing to be told about.
    pub async fn connect() -> Result<Self> {
        let connection = zbus::Connection::session()
            .await
            .context("no session bus")?;
        let proxy = zbus::proxy::Builder::new(&connection)
            .destination(BUS_NAME)?
            .path(OBJECT_PATH)?
            .interface(INTERFACE)?
            .cache_properties(zbus::proxy::CacheProperties::No)
            .build()
            .await
            .context("the daemon is not on the session bus")?;
        Ok(Self { proxy })
    }

    /// One method that answers with a JSON string, parsed.
    async fn json(&self, method: &str) -> Result<Value> {
        let reply: String = self
            .proxy
            .call(method, &())
            .await
            .with_context(|| format!("{method} failed"))?;
        serde_json::from_str(&reply).with_context(|| format!("{method} did not answer with JSON"))
    }

    pub async fn status(&self) -> Result<Value> {
        self.json("GetStatus").await
    }

    pub async fn health(&self) -> Result<Value> {
        self.json("GetHealth").await
    }

    pub async fn sessions(&self) -> Result<Value> {
        self.json("GetSessions").await
    }

    /// One game's history, or every game's when `exe` is empty - which is what
    /// the interface means by an empty string here, not "no games".
    pub async fn session_history(&self, exe: &str) -> Result<Value> {
        let reply: String = self
            .proxy
            .call("GetSessionHistory", &(exe))
            .await
            .context("GetSessionHistory failed")?;
        serde_json::from_str(&reply).context("GetSessionHistory did not answer with JSON")
    }

    /// Runs the checks. It reads and reports; the fixing is a different method
    /// and this CLI does not call it.
    pub async fn preflight(&self) -> Result<Value> {
        self.json("RunPreflight").await
    }
}
