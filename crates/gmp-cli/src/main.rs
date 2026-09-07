//! `gmp-cli` - the Rust command line, talking to whichever daemon is running.
//!
//! It renders through the same functions the parity harness grades against the
//! Python, so a line it prints is a line `goblin-mode-pro-cli` prints. What is
//! here is only the asking: connect, call, hand the reply to the renderer.

use anyhow::Result;

use gmp_cli::{bus, report};

const USAGE: &str = "\
goblin mode pro - command line

usage: gmp-cli <command>

commands:
  status      what the daemon is doing right now
  help        this
";

#[tokio::main]
async fn main() -> std::process::ExitCode {
    match run().await {
        Ok(code) => code,
        Err(err) => {
            // The chain, because the useful half is usually the cause: "no
            // session bus" and "the daemon is not on the session bus" are
            // different problems with the same first line.
            eprintln!("gmp-cli: {err:#}");
            std::process::ExitCode::FAILURE
        }
    }
}

async fn run() -> Result<std::process::ExitCode> {
    let command = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "help".to_string());
    match command.as_str() {
        "status" => {
            let daemon = bus::Daemon::connect().await?;
            for line in report::status(&daemon.status().await?) {
                println!("{line}");
            }
            Ok(std::process::ExitCode::SUCCESS)
        }
        "help" | "--help" | "-h" => {
            print!("{USAGE}");
            Ok(std::process::ExitCode::SUCCESS)
        }
        other => {
            eprintln!("gmp-cli: unknown command {other:?}\n");
            eprint!("{USAGE}");
            Ok(std::process::ExitCode::FAILURE)
        }
    }
}
