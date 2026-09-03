//! The command-line surface: how a selftest run is reported.
//!
//! The first slice of the daemon/CLI port, and deliberately the reporting
//! layer rather than the probing. `selftest` is the tool that verifies
//! everything else on real hardware, so it is worth having early - but the
//! probes themselves are the part that must keep talking to this machine's
//! sysfs, and they stay in Python for now.
//!
//! What is here is the part a person actually reads: the ordering, the
//! alignment, the counts line, and the sentence shown when a helper call
//! fails. That last one is the reason this slice is worth porting at all -
//! it is the text somebody sees at the exact moment the tool is not working,
//! and it has to say the same thing whichever implementation produced it.

pub mod report;
pub mod selftest;
