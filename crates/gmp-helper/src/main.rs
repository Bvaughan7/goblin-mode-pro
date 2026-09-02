mod polkit;
mod state;

fn main() {
    // Placeholder: the D-Bus service is wired up next. The modules above are
    // the security-critical core and are already under test.
    tracing_subscriber::fmt::init();
    tracing::info!("gmp-helper skeleton");
}
