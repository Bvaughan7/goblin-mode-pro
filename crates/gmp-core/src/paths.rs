//! Where this program's files live.
//!
//! Every location is derived from the XDG base directory spec, so the daemon
//! (a systemd *user* service), the GUI and the `goblin-run` wrapper all agree
//! without any of them hard-coding a home directory. Three processes reading
//! the same files is the entire reason this has to be exact: a daemon and a
//! GUI that disagree about where `config.json` is do not fail loudly, they
//! quietly stop seeing each other's writes.
//!
//! Derivation only. Nothing here touches the filesystem or reads the real
//! environment - both are passed in - which is what makes it diffable against
//! the Python from fixtures.
//!
//! Mirrors `src/goblinmode/paths.py`.

pub const APP_DIRNAME: &str = "goblin-mode-pro";

/// The pieces of the environment the paths are derived from.
///
/// Taken as data rather than read here, so a test can ask what a machine that
/// is not this one would resolve to.
#[derive(Debug, Clone, Default)]
pub struct Env {
    pub home: String,
    pub config_home: Option<String>,
    pub state_home: Option<String>,
    pub data_home: Option<String>,
    pub cache_home: Option<String>,
}

impl Env {
    /// Read the real environment. `HOME` is the only variable without a
    /// sensible fallback, so an absent one is left empty and every derived
    /// path becomes relative - which is wrong, but it is exactly as wrong as
    /// the Python and is not this layer's problem to invent a fix for.
    pub fn from_process() -> Self {
        let var = |name: &str| std::env::var(name).ok();
        Self {
            home: var("HOME").unwrap_or_default(),
            config_home: var("XDG_CONFIG_HOME"),
            state_home: var("XDG_STATE_HOME"),
            data_home: var("XDG_DATA_HOME"),
            cache_home: var("XDG_CACHE_HOME"),
        }
    }
}

/// Every location, resolved.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Paths {
    pub config_dir: String,
    pub state_dir: String,
    pub data_dir: String,
    pub cache_dir: String,
    pub config_file: String,
    pub game_log_dir: String,
    pub incident_file: String,
    pub session_file: String,
    pub mangohud_log_dir: String,
    pub applied_state_file: String,
    pub onboarded_marker: String,
    pub mangohud_dir: String,
    pub mangohud_conf: String,
    pub local_bin: String,
    pub runner_wrapper: String,
    pub helper_runtime_dir: String,
    pub helper_state_file: String,
}

/// A POSIX path, kept as its anchor and its components rather than as a
/// string, because `pathlib` normalises and string concatenation does not.
///
/// The rules, taken from CPython rather than assumed: empty and `.`
/// components are dropped, `..` is kept (resolving it would need the
/// filesystem), repeated separators collapse - and exactly TWO leading
/// slashes are preserved, which POSIX reserves for the implementation while
/// three or more collapse to one.
#[derive(Debug, Clone)]
struct PurePath {
    anchor: &'static str,
    parts: Vec<String>,
}

impl PurePath {
    fn parse(raw: &str) -> Self {
        let anchor = if raw.starts_with("///") {
            "/"
        } else if raw.starts_with("//") {
            "//"
        } else if raw.starts_with('/') {
            "/"
        } else {
            ""
        };
        let parts = raw
            .split('/')
            .filter(|part| !part.is_empty() && *part != ".")
            .map(str::to_string)
            .collect();
        Self { anchor, parts }
    }

    /// `self / child`. An absolute child replaces the whole path, which is
    /// what `pathlib` does and what makes `XDG_DATA_HOME=/data` work.
    fn join(&self, child: &str) -> Self {
        if child.starts_with('/') {
            return Self::parse(child);
        }
        let mut joined = self.clone();
        joined.parts.extend(Self::parse(child).parts);
        joined
    }
}

impl std::fmt::Display for PurePath {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.parts.is_empty() {
            // `str(Path(""))` and `str(Path("."))` are both ".".
            return write!(
                f,
                "{}",
                if self.anchor.is_empty() {
                    "."
                } else {
                    self.anchor
                }
            );
        }
        write!(f, "{}{}", self.anchor, self.parts.join("/"))
    }
}

fn join(base: &str, child: &str) -> String {
    PurePath::parse(base).join(child).to_string()
}

/// `Path(raw).expanduser()`, for the only `~` form either side accepts.
///
/// A bare `~` and a leading `~/` expand against `HOME`. `~someone` does NOT,
/// on both sides, and that is a decision rather than a gap: `pathlib` raises
/// for a user who does not exist, and `paths.py` is imported by the daemon,
/// the GUI, the CLI and the launch wrapper, so one exotic variable stopped all
/// four from starting. Refusing it also means neither implementation has to
/// consult a password database to agree with the other - and pointing an XDG
/// base at another account's home is not worth obeying even when it resolves.
///
/// Returns `None` when the value is a `~` form that is not accepted, so the
/// caller falls back to the default.
fn expanduser(raw: &str, home: &str) -> Option<String> {
    // Done on the parsed components rather than on the string, because the
    // separators collapse first: `~//x` is `~/x` to pathlib and expands to
    // `<home>/x`. Matching on the `"~/"` prefix of the raw text instead left a
    // leading slash on the remainder, which then read as an absolute path and
    // threw the home directory away entirely.
    let path = PurePath::parse(raw);
    let first = path.parts.first().map(String::as_str).unwrap_or_default();
    if !path.anchor.is_empty() || !first.starts_with('~') {
        return Some(path.to_string());
    }
    if first != "~" {
        return None; // `~someone` - refused, see above.
    }
    let mut expanded = PurePath::parse(home);
    expanded.parts.extend(path.parts[1..].iter().cloned());
    Some(expanded.to_string())
}

/// The XDG base directory `value` names, or `default`.
///
/// A variable that is set but EMPTY counts as unset - that is what the spec
/// says, and it is what an environment that clears a variable rather than
/// unsetting it produces. Whitespace-only is treated the same way: it is not a
/// path anybody meant, and the alternative is a directory literally named " ".
fn xdg_base(value: Option<&str>, default: String, home: &str) -> String {
    match value.map(str::trim).filter(|raw| !raw.is_empty()) {
        Some(raw) => expanduser(raw, home).unwrap_or(default),
        None => default,
    }
}

/// Resolve every location from `env`.
pub fn resolve(env: &Env) -> Paths {
    let home = env.home.as_str();
    let base = |value: &Option<String>, default: &str| {
        xdg_base(value.as_deref(), join(home, default), home)
    };

    let config_base = base(&env.config_home, ".config");
    let state_base = base(&env.state_home, ".local/state");
    let data_base = base(&env.data_home, ".local/share");
    let cache_base = base(&env.cache_home, ".cache");

    let config_dir = join(&config_base, APP_DIRNAME);
    let state_dir = join(&state_base, APP_DIRNAME);
    let data_dir = join(&data_base, APP_DIRNAME);
    let cache_dir = join(&cache_base, APP_DIRNAME);

    // MangoHud's own directory, NOT namespaced under the app name - but
    // resolved from the same base as everything else. Deriving it separately
    // is what made an empty XDG_CONFIG_HOME produce a relative path.
    let mangohud_dir = join(&config_base, "MangoHud");
    let local_bin = join(home, ".local/bin");
    let helper_runtime_dir = join("/run", APP_DIRNAME);

    Paths {
        config_file: join(&config_dir, "config.json"),
        game_log_dir: join(&data_dir, "logs"),
        incident_file: join(&data_dir, "incidents.jsonl"),
        session_file: join(&data_dir, "sessions.jsonl"),
        mangohud_log_dir: join(&data_dir, "mangohud"),
        applied_state_file: join(&state_dir, "applied.json"),
        onboarded_marker: join(&state_dir, "onboarded"),
        mangohud_conf: join(&mangohud_dir, "MangoHud.conf"),
        runner_wrapper: join(&local_bin, "goblin-run"),
        helper_state_file: join(&helper_runtime_dir, "state.json"),
        config_dir,
        state_dir,
        data_dir,
        cache_dir,
        mangohud_dir,
        local_bin,
        helper_runtime_dir,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(config_home: Option<&str>) -> Env {
        Env {
            home: "/home/u".to_string(),
            config_home: config_home.map(str::to_string),
            ..Env::default()
        }
    }

    #[test]
    fn the_defaults_are_the_xdg_ones() {
        let p = resolve(&env(None));
        assert_eq!(p.config_dir, "/home/u/.config/goblin-mode-pro");
        assert_eq!(p.state_dir, "/home/u/.local/state/goblin-mode-pro");
        assert_eq!(p.data_dir, "/home/u/.local/share/goblin-mode-pro");
        assert_eq!(p.cache_dir, "/home/u/.cache/goblin-mode-pro");
        assert_eq!(p.mangohud_dir, "/home/u/.config/MangoHud");
    }

    #[test]
    fn a_variable_that_is_set_but_empty_counts_as_unset() {
        // The spec's way of saying "unset", and what several launchers produce.
        for raw in ["", "   ", "\t", "\n"] {
            let p = resolve(&env(Some(raw)));
            assert_eq!(p.config_dir, "/home/u/.config/goblin-mode-pro", "{raw:?}");
            // This is the one that used to come out as the relative path
            // "MangoHud", which meant MANGOHUD_CONFIGFILE was exported to the
            // game relative to ITS working directory and never applied.
            assert_eq!(p.mangohud_dir, "/home/u/.config/MangoHud", "{raw:?}");
            assert!(p.mangohud_conf.starts_with('/'), "{raw:?}");
        }
    }

    #[test]
    fn mangohud_follows_the_same_base_as_everything_else() {
        let p = resolve(&env(Some("/tmp/cfg")));
        assert_eq!(p.config_dir, "/tmp/cfg/goblin-mode-pro");
        assert_eq!(p.mangohud_dir, "/tmp/cfg/MangoHud");
        assert_eq!(p.mangohud_conf, "/tmp/cfg/MangoHud/MangoHud.conf");
    }

    #[test]
    fn a_tilde_expands_against_home() {
        assert_eq!(
            resolve(&env(Some("~/cfg"))).config_dir,
            "/home/u/cfg/goblin-mode-pro"
        );
        assert_eq!(
            resolve(&env(Some("~"))).config_dir,
            "/home/u/goblin-mode-pro"
        );
    }

    #[test]
    fn a_trailing_separator_is_not_doubled() {
        assert_eq!(
            resolve(&env(Some("/tmp/cfg/"))).config_dir,
            "/tmp/cfg/goblin-mode-pro"
        );
    }

    #[test]
    fn surrounding_whitespace_is_trimmed_off_a_real_path() {
        assert_eq!(
            resolve(&env(Some("  /tmp/cfg  "))).config_dir,
            "/tmp/cfg/goblin-mode-pro"
        );
    }

    #[test]
    fn a_tilde_expands_even_with_the_separators_doubled() {
        // `~//x` is `~/x` once pathlib has collapsed the separators. Matching
        // on the raw prefix left "/x", which read as absolute and discarded
        // the home directory.
        assert_eq!(
            resolve(&env(Some("~//x"))).config_dir,
            "/home/u/x/goblin-mode-pro"
        );
        assert_eq!(
            resolve(&env(Some("~//"))).config_dir,
            "/home/u/goblin-mode-pro"
        );
    }

    #[test]
    fn a_tilde_only_expands_as_the_first_component_of_a_relative_path() {
        // Anywhere else it is an ordinary directory name, which is exactly
        // what pathlib does with it.
        assert_eq!(
            resolve(&env(Some("/~/cfg"))).config_dir,
            "/~/cfg/goblin-mode-pro"
        );
        assert_eq!(
            resolve(&env(Some("cfg/~/x"))).config_dir,
            "cfg/~/x/goblin-mode-pro"
        );
    }

    #[test]
    fn a_username_after_the_tilde_falls_back_to_the_default() {
        // Refused on both sides. pathlib RAISES for a user who does not
        // exist, and paths.py is imported by four processes, so honouring it
        // meant one exotic variable stopped all four from starting.
        for raw in ["~someone/cfg", "~root", "~nosuchuser"] {
            assert_eq!(
                resolve(&env(Some(raw))).config_dir,
                "/home/u/.config/goblin-mode-pro",
                "{raw}"
            );
        }
    }

    #[test]
    fn exactly_two_leading_slashes_are_preserved() {
        // POSIX reserves `//` for the implementation, and pathlib keeps it.
        assert_eq!(
            resolve(&env(Some("//srv"))).config_dir,
            "//srv/goblin-mode-pro"
        );
        assert_eq!(
            resolve(&env(Some("///srv"))).config_dir,
            "/srv/goblin-mode-pro"
        );
        assert_eq!(resolve(&env(Some("//"))).config_dir, "//goblin-mode-pro");
    }

    #[test]
    fn a_path_with_no_components_renders_the_way_pathlib_renders_it() {
        // `str(Path(""))` and `str(Path("."))` are both ".", and a bare anchor
        // renders as itself. Unreachable from `resolve`, so pinned directly.
        assert_eq!(PurePath::parse("").to_string(), ".");
        assert_eq!(PurePath::parse(".").to_string(), ".");
        assert_eq!(PurePath::parse("./.").to_string(), ".");
        assert_eq!(PurePath::parse("/").to_string(), "/");
        assert_eq!(PurePath::parse("//").to_string(), "//");
    }

    #[test]
    fn an_absolute_child_replaces_the_whole_path() {
        // pathlib's rule. No caller in this module passes one, so it is pinned
        // here rather than through a resolved location.
        assert_eq!(PurePath::parse("/a/b").join("/c").to_string(), "/c");
        assert_eq!(PurePath::parse("/a/b").join("c").to_string(), "/a/b/c");
    }

    #[test]
    fn the_helper_runtime_dir_does_not_depend_on_the_environment() {
        // It is root-owned tmpfs shared with a process running as root, so a
        // user's XDG settings must not move it.
        let p = resolve(&env(Some("/tmp/cfg")));
        assert_eq!(p.helper_runtime_dir, "/run/goblin-mode-pro");
        assert_eq!(p.helper_state_file, "/run/goblin-mode-pro/state.json");
    }
}
