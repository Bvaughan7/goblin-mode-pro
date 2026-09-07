//! `gmp-cli` - the Rust command line, talking to whichever daemon is running.
//!
//! It renders through the same functions the parity harness grades against the
//! Python, so a line it prints is a line `goblin-mode-pro-cli` prints. What is
//! here is only the asking: connect, call, hand the reply to the renderer.

use anyhow::Result;

use gmp_cli::{bus, report};

const USAGE: &str = "\
goblin mode pro - command line

usage: gmp-cli <command> [options]

commands:
  status              what the daemon is doing right now
  health              the pre-flight score, cached
  games               the profiles this daemon knows, and which are running
  sessions [--game X] [--limit N]
                      recent sessions, newest first (default 15)
  preflight           run the pre-flight checks and report them
  help                this

Only reads. Nothing here changes a setting or touches the machine.
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
    let args: Vec<String> = std::env::args().skip(2).collect();
    match command.as_str() {
        "status" => print_lines(report::status(&connect().await?.status().await?)),
        "health" => print_lines(report::health(&connect().await?.health().await?)),
        "games" => print_lines(report::games(&connect().await?.status().await?)),
        "preflight" => {
            let checks = connect().await?.preflight().await?;
            print_lines(report::preflight(rows(&checks)))
        }
        "sessions" => {
            let game = option(&args, "--game").unwrap_or_default();
            let limit = match option(&args, "--limit") {
                Some(text) => text
                    .parse::<i64>()
                    .map_err(|_| anyhow::anyhow!("--limit wants a number, not {text:?}"))?,
                None => 15,
            };
            let history = connect().await?.session_history(&game).await?;
            print_lines(report::sessions(rows(&history), limit))
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

async fn connect() -> Result<bus::Daemon> {
    bus::Daemon::connect().await
}

fn print_lines(lines: Vec<String>) -> Result<std::process::ExitCode> {
    for line in lines {
        println!("{line}");
    }
    Ok(std::process::ExitCode::SUCCESS)
}

/// A reply that should be a list of rows, as one.
///
/// Not an error when it is something else: these replies cross the interface
/// as JSON strings, so their shape is not guaranteed by the signature, and a
/// renderer handed nothing prints the empty case it already knows how to
/// print.
fn rows(value: &serde_json::Value) -> &[serde_json::Value] {
    value.as_array().map_or(&[], Vec::as_slice)
}

/// `--flag value`, the way the Python CLI's argparse takes it.
fn option(args: &[String], flag: &str) -> Option<String> {
    let at = args.iter().position(|arg| arg == flag)?;
    args.get(at + 1).cloned()
}
