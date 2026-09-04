//! The session-bus daemon: the interface the GUI and the CLI talk to.
//!
//! Split into a library and a thin binary from the start. The conversion plan
//! asks specifically that this crate not re-accumulate the way the Python
//! daemon did - 880 lines in one file - so lifecycle, api and state are
//! separate modules and the entry point does nothing but choose between them.
//!
//! This block serves the frozen contract in
//! `docs/dbus-daemon-interface-v1.xml` and nothing else: every method refuses
//! with `NotImplemented`. Nothing installs or runs it; the Python daemon is
//! still the one on this machine and on everyone else's. What it buys today is
//! that the freeze check and `tests/conformance/daemon.py` can both grade it,
//! so the shape is settled before any behaviour depends on it.

pub mod api;
pub mod error;
pub mod helper;
pub mod lifecycle;
pub mod state;
pub mod store;
