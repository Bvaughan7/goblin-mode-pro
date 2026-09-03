//! Domain logic for Goblin Mode Pro.
//!
//! Everything here is testable from fixture strings alone: no D-Bus, no sysfs
//! policy, no GUI, no privileged anything. That is the point of the crate -
//! the parts of the application that are just rules can be moved across a
//! language boundary without moving any of the plumbing with them.
//!
//! The Python implementation in `src/goblinmode/` remains the shipped one
//! while this is built. Both are expected to agree, and the tests are
//! translated from the Python module's own tests rather than written afresh,
//! so that a disagreement shows up as a failure rather than as an opinion.

pub mod capabilities;
pub mod compositor;
pub mod diagnostics;
pub mod gamedetect;
pub mod gpu;
pub mod incidents;
pub mod logrules;
pub mod preflight;
pub(crate) mod round;
pub mod scx;
pub mod sessions;
