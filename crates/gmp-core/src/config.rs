//! The on-disk profile schema, and the normalisation every load runs.
//!
//! A port of `src/goblinmode/config.py`. This is the module where a mistake
//! costs a user their settings rather than a frame rate, so two rules shape
//! the whole thing.
//!
//! **Unknown keys are dropped, not preserved.** That is what the Python does -
//! `_from_dict` filters against the dataclass fields, and `save` writes only
//! known ones - so a key written by a newer build does not survive an older
//! build reading and saving the file. It is a real property of the format,
//! with real consequences, and the port reproduces it exactly rather than
//! quietly improving on it: a Rust that preserved unknown keys would round-trip
//! a file the Python would not, and the two would no longer be interchangeable.
//! Changing it is a schema decision to take deliberately, on both sides at once.
//!
//! **Every field has a default and nothing is required except `exe`.** A
//! profile missing a key gets the default; a profile with a key of the wrong
//! type is dropped whole rather than half-applied. `deny_unknown_fields` is
//! never used.

use serde::{Deserialize, Serialize};

use crate::config_tables::{
    default_gamescope, CORE_PIN_MODES, DEFAULT_MANGOHUD, DEFAULT_RUNNER_VARS, GAMESCOPE_UPSCALERS,
    GPU_TUNING_VARS, MATCH_MODES, RUNNER_VARS, SCX_MODES,
};
use crate::scx::valid_name as valid_scx_name;

pub const SCHEMA_VERSION: i64 = 1;

/// Take the first `n` CHARACTERS, as Python's slicing does.
///
/// Not bytes: `display_name[:200]` on a name in Cyrillic or Japanese keeps 200
/// characters, and a byte-based truncation would both keep the wrong amount
/// and be capable of splitting a character in half.
fn take_chars(value: &str, n: usize) -> String {
    value.chars().take(n).collect()
}

/// Python's `int(x)` where x may be a bool, a float or an int.
///
/// Floats truncate toward zero, and `True` is 1 - both are Python's rules, and
/// both reach this code through a hand-edited config file. A string, or
/// anything else, is an error: the Python raises and drops the whole profile,
/// which is the behaviour worth keeping. Half-applying a corrupt profile is
/// how a user ends up with a machine tuned by a file they cannot read.
fn py_int(value: &serde_json::Value) -> Option<i64> {
    match value {
        serde_json::Value::Bool(b) => Some(i64::from(*b)),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i)
            } else {
                let f = n.as_f64()?;
                (f.is_finite() && f.abs() < 9.3e18).then(|| f.trunc() as i64)
            }
        }
        // int("9") is 9. A config edited by hand quotes numbers all the time,
        // and the Python accepts it - but int("9.5") still raises, so this is
        // the integer grammar, not a float parse followed by a truncation.
        serde_json::Value::String(text) => parse_py_digits(text.trim()),
        _ => None,
    }
}

/// Python's integer literal grammar: optional sign, digits, single
/// underscores between digits.
fn parse_py_digits(text: &str) -> Option<i64> {
    let (negative, digits) = match text.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, text.strip_prefix('+').unwrap_or(text)),
    };
    if digits.is_empty() || digits.starts_with('_') || digits.ends_with('_') {
        return None;
    }
    let mut value: i64 = 0;
    let mut previous_underscore = false;
    for c in digits.chars() {
        if c == '_' {
            if previous_underscore {
                return None;
            }
            previous_underscore = true;
            continue;
        }
        previous_underscore = false;
        let digit = c.to_digit(10)?;
        value = value.checked_mul(10)?.checked_add(i64::from(digit))?;
    }
    Some(if negative { -value } else { value })
}

fn py_float(value: &serde_json::Value) -> Option<f64> {
    match value {
        serde_json::Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        serde_json::Value::Number(n) => n.as_f64(),
        serde_json::Value::String(text) => {
            let text = text.trim();
            if text.contains('_') {
                let bytes = text.as_bytes();
                for (i, b) in bytes.iter().enumerate() {
                    if *b != b'_' {
                        continue;
                    }
                    if !i
                        .checked_sub(1)
                        .map(|j| bytes[j])
                        .is_some_and(|c| c.is_ascii_digit())
                        || !bytes
                            .get(i + 1)
                            .copied()
                            .is_some_and(|c| c.is_ascii_digit())
                    {
                        return None;
                    }
                }
                return text.replace('_', "").parse().ok();
            }
            text.parse().ok()
        }
        _ => None,
    }
}

/// Python's truthiness for the values that reach `bool(v)` here.
fn py_bool(value: &serde_json::Value) -> bool {
    match value {
        serde_json::Value::Null => false,
        serde_json::Value::Bool(b) => *b,
        serde_json::Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        serde_json::Value::String(s) => !s.is_empty(),
        serde_json::Value::Array(a) => !a.is_empty(),
        serde_json::Value::Object(o) => !o.is_empty(),
    }
}

/// `value in choices`, which is simply false when `value` is not a string.
fn in_choices(value: &serde_json::Value, choices: &[&str]) -> bool {
    value.as_str().is_some_and(|s| choices.contains(&s))
}

/// What `for x in value` yields in Python, for the shapes that reach here.
///
/// A string iterates by CHARACTER, which is the surprising one and the reason
/// this is spelled out: `vrr_outputs: "abc"` is three outputs in Python, not
/// an error. A number is not iterable and raises, dropping the profile.
fn py_iter(value: &serde_json::Value) -> Option<Vec<serde_json::Value>> {
    match value {
        // `self.vrr_outputs or []` - a falsy value becomes the empty list.
        serde_json::Value::Null => Some(Vec::new()),
        serde_json::Value::Array(a) if a.is_empty() => Some(Vec::new()),
        serde_json::Value::Array(a) => Some(a.clone()),
        serde_json::Value::String(s) if s.is_empty() => Some(Vec::new()),
        serde_json::Value::String(s) => Some(
            s.chars()
                .map(|c| serde_json::json!(c.to_string()))
                .collect(),
        ),
        serde_json::Value::Object(o) if o.is_empty() => Some(Vec::new()),
        serde_json::Value::Object(o) => Some(o.keys().map(|k| serde_json::json!(k)).collect()),
        serde_json::Value::Bool(false) => Some(Vec::new()),
        serde_json::Value::Number(n) if n.as_f64() == Some(0.0) => Some(Vec::new()),
        _ => None,
    }
}

/// `str(value or "")` - a falsy value becomes the empty string rather than
/// the word "None", which is what a bare `str(None)` would give.
fn py_str_or_empty(value: &serde_json::Value) -> String {
    if py_bool(value) {
        py_str(value)
    } else {
        String::new()
    }
}

/// A mapping as ordered key/value pairs.
type Pairs = Vec<(String, serde_json::Value)>;

/// `dict(value or {})` for the shapes that reach it.
fn py_dict(value: &serde_json::Value) -> Option<Pairs> {
    if !py_bool(value) {
        return Some(Vec::new());
    }
    match value {
        serde_json::Value::Object(o) => {
            Some(o.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        }
        // dict([["a", 1]]) is a mapping too; dict("x") is not.
        serde_json::Value::Array(items) => items
            .iter()
            .map(|item| {
                let pair = item.as_array()?;
                let [key, value] = pair.as_slice() else {
                    return None;
                };
                Some((key.as_str()?.to_string(), value.clone()))
            })
            .collect(),
        _ => None,
    }
}

/// `max(low, min(high, value))` with PYTHON's comparison, not `f64::clamp`.
///
/// The three disagree on NaN, and NaN is reachable: `fps_dip_ratio: "nan"` in
/// a config file becomes one. Python's `min` returns its first argument when
/// the comparison is false, so a NaN comes out as `high`; `f64::max`/`min`
/// discard NaN and would give `low`; `clamp` panics on it in debug builds.
fn py_clamp(value: f64, low: f64, high: f64) -> f64 {
    let capped = if value < high { value } else { high };
    if capped > low {
        capped
    } else {
        low
    }
}

fn clamp_i64(value: i64, low: i64, high: i64) -> i64 {
    value.max(low).min(high)
}

/// Validate a profile's `exe` token.
///
/// An `exe` may hold an exact name, a substring, or a regex pattern, so
/// metacharacters are allowed - but never a path separator, `..`, NUL or a
/// control character, and the length is bounded as a ReDoS guard. Callers pass
/// a plain name; splitting a path is their job.
pub fn sanitize_exe(value: &str) -> Result<String, String> {
    let trimmed = value.trim().trim_matches(['"', '\'']);
    let bad = trimmed.contains("..")
        || trimmed
            .chars()
            .any(|c| c == '/' || c == '\\' || (c as u32) < 0x20 || c as u32 == 0x7f);
    if trimmed.is_empty() || trimmed.chars().count() > 128 || trimmed == "." || bad {
        return Err(format!("invalid game executable name: {trimmed:?}"));
    }
    Ok(trimmed.to_owned())
}

/// A filesystem-safe token derived from a name, for per-game config files.
pub fn slug(value: &str) -> String {
    let mut out = String::new();
    let mut in_run = false;
    for c in value.chars() {
        if c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-' {
            out.push(c);
            in_run = false;
        } else if !in_run {
            out.push('_');
            in_run = true;
        }
    }
    let stripped = out.trim_matches(['.', '_', '-']);
    let cut = take_chars(stripped, 80);
    if cut.is_empty() {
        "game".to_string()
    } else {
        cut
    }
}

/// A profile as it is stored, before normalisation.
///
/// Every field carries `#[serde(default)]` and the struct does NOT use
/// `deny_unknown_fields`: an unrecognised key is ignored, matching the
/// Python's field filter.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct GameProfile {
    pub exe: String,
    pub display_name: String,
    pub enabled: serde_json::Value,
    pub match_mode: serde_json::Value,
    pub auto_created: serde_json::Value,
    pub renice_enabled: serde_json::Value,
    pub nice_value: serde_json::Value,
    pub use_gamemode: serde_json::Value,
    pub core_pin: serde_json::Value,
    pub scx_scheduler: serde_json::Value,
    pub scx_mode: serde_json::Value,
    pub tearing_enabled: serde_json::Value,
    pub refresh_rate_hz: serde_json::Value,
    pub adaptive_sync_enabled: serde_json::Value,
    pub vrr_outputs: serde_json::Value,
    pub governor_boost: serde_json::Value,
    pub focus_mode: serde_json::Value,
    pub power_limit_enabled: serde_json::Value,
    pub pl1_w: serde_json::Value,
    pub pl2_w: serde_json::Value,
    pub battery_pl1_w: serde_json::Value,
    pub battery_pl2_w: serde_json::Value,
    pub undervolt_reapply: serde_json::Value,
    pub amd_undervolt_reapply: serde_json::Value,
    pub fan_spinup_enabled: serde_json::Value,
    pub per_game_mangohud: serde_json::Value,
    pub mangohud: serde_json::Map<String, serde_json::Value>,
    pub fps_watchdog: serde_json::Value,
    pub fps_dip_floor: serde_json::Value,
    pub fps_dip_ratio: serde_json::Value,
    pub clip_on_incident: serde_json::Value,
    pub runner_vars: serde_json::Map<String, serde_json::Value>,
    pub gamescope_enabled: serde_json::Value,
    pub gamescope: serde_json::Map<String, serde_json::Value>,
    pub gpu_tuning: serde_json::Value,
    pub steam_app_id: serde_json::Value,
    pub notes: serde_json::Value,
}

impl Default for GameProfile {
    fn default() -> Self {
        Self {
            exe: String::new(),
            display_name: String::new(),
            enabled: serde_json::json!(true),
            match_mode: serde_json::json!("exact"),
            auto_created: serde_json::json!(false),
            renice_enabled: serde_json::json!(true),
            nice_value: serde_json::json!(-5),
            use_gamemode: serde_json::json!(true),
            core_pin: serde_json::json!("off"),
            scx_scheduler: serde_json::json!(""),
            scx_mode: serde_json::json!("gaming"),
            tearing_enabled: serde_json::json!(true),
            refresh_rate_hz: serde_json::json!(0),
            adaptive_sync_enabled: serde_json::json!(false),
            vrr_outputs: serde_json::json!([]),
            governor_boost: serde_json::json!(true),
            focus_mode: serde_json::json!(false),
            power_limit_enabled: serde_json::json!(false),
            pl1_w: serde_json::json!(0),
            pl2_w: serde_json::json!(0),
            battery_pl1_w: serde_json::json!(0),
            battery_pl2_w: serde_json::json!(0),
            undervolt_reapply: serde_json::json!(false),
            amd_undervolt_reapply: serde_json::json!(false),
            fan_spinup_enabled: serde_json::json!(false),
            per_game_mangohud: serde_json::json!(false),
            mangohud: defaults_map(DEFAULT_MANGOHUD),
            fps_watchdog: serde_json::json!(false),
            fps_dip_floor: serde_json::json!(22),
            fps_dip_ratio: serde_json::json!(0.5),
            clip_on_incident: serde_json::json!(false),
            runner_vars: defaults_map(DEFAULT_RUNNER_VARS),
            gamescope_enabled: serde_json::json!(false),
            gamescope: default_gamescope(),
            gpu_tuning: serde_json::json!({}),
            steam_app_id: serde_json::json!(""),
            notes: serde_json::json!(""),
        }
    }
}

fn defaults_map(pairs: &[(&str, bool)]) -> serde_json::Map<String, serde_json::Value> {
    pairs
        .iter()
        .map(|(k, v)| ((*k).to_string(), serde_json::json!(v)))
        .collect()
}

impl GameProfile {
    /// `__post_init__`: validate, clamp and fill in.
    ///
    /// An `Err` means the Python would have raised, which drops the profile.
    pub fn normalise(&mut self) -> Result<(), String> {
        self.exe = sanitize_exe(&self.exe)?;
        if self.display_name.is_empty() {
            self.display_name = self.exe.clone();
        }
        self.display_name = take_chars(&self.display_name, 200);
        // `x not in MATCH_MODES` is false for a non-string too, so a
        // wrong-typed enum falls back rather than dropping the profile.
        if !in_choices(&self.match_mode, MATCH_MODES) {
            self.match_mode = serde_json::json!("exact");
        }
        if !in_choices(&self.core_pin, CORE_PIN_MODES) {
            self.core_pin = serde_json::json!("off");
        }
        if py_bool(&self.scx_scheduler) {
            // `if self.scx_scheduler:` then `.strip()`, so a truthy non-string
            // raises in the Python and takes the profile with it.
            let raw = self
                .scx_scheduler
                .as_str()
                .ok_or("scx_scheduler is not a string")?;
            // Accept "lavd" or "scx_lavd"; store the short form.
            let name = crate::scx::short_name(raw.trim());
            self.scx_scheduler = serde_json::json!(if valid_scx_name(&name) {
                name
            } else {
                String::new()
            });
        }
        if !in_choices(&self.scx_mode, SCX_MODES) {
            self.scx_mode = serde_json::json!("gaming");
        }

        self.nice_value = clamped(&self.nice_value, -10, 19)?;
        self.pl1_w = clamped(&self.pl1_w, 0, 500)?;
        self.pl2_w = clamped(&self.pl2_w, 0, 500)?;
        self.battery_pl1_w = clamped(&self.battery_pl1_w, 0, 500)?;
        self.battery_pl2_w = clamped(&self.battery_pl2_w, 0, 500)?;
        self.refresh_rate_hz = clamped(&self.refresh_rate_hz, 0, 1000)?;
        self.fps_dip_floor = clamped(&self.fps_dip_floor, 5, 120)?;

        let ratio = py_float(&self.fps_dip_ratio).ok_or("fps_dip_ratio is not a number")?;
        self.fps_dip_ratio = serde_json::json!(py_clamp(ratio, 0.1, 0.9));

        // `[str(o)[:64] for o in (self.vrr_outputs or [])][:16]` - and in
        // Python that comprehension iterates a STRING character by character,
        // so "abc" becomes three outputs rather than raising. Reproduced,
        // because the alternative is a profile that loads on one side only.
        let items = py_iter(&self.vrr_outputs).ok_or("vrr_outputs is not iterable")?;
        self.vrr_outputs = serde_json::Value::Array(
            items
                .iter()
                .take(16)
                .map(|o| serde_json::Value::String(take_chars(&py_str(o), 64)))
                .collect(),
        );

        // Fill in any keys added in newer versions. setdefault, so a value the
        // user set is never overwritten by a default.
        for (key, value) in DEFAULT_MANGOHUD {
            self.mangohud
                .entry((*key).to_string())
                .or_insert_with(|| serde_json::json!(value));
        }
        for (key, value) in DEFAULT_RUNNER_VARS {
            self.runner_vars
                .entry((*key).to_string())
                .or_insert_with(|| serde_json::json!(value));
        }
        for (key, value) in default_gamescope() {
            self.gamescope.entry(key).or_insert(value);
        }
        for key in ["w", "h", "refresh"] {
            let raw = self
                .gamescope
                .get(key)
                .cloned()
                .unwrap_or(serde_json::json!(0));
            // `int(x or 0)` - a falsy value becomes 0 before conversion, which
            // is what lets a null or an empty string through where a bare
            // int() would raise.
            let value = if py_bool(&raw) {
                py_int(&raw).ok_or_else(|| format!("gamescope.{key} is not a number"))?
            } else {
                0
            };
            self.gamescope.insert(
                key.to_string(),
                serde_json::json!(clamp_i64(value, 0, 10000)),
            );
        }
        let upscale = self
            .gamescope
            .get("upscale")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("");
        if !GAMESCOPE_UPSCALERS.contains(&upscale) {
            self.gamescope
                .insert("upscale".into(), serde_json::json!("off"));
        }

        // A bare number if set: every non-digit is stripped, so "app 12345"
        // and "#12345" both become "12345" rather than being rejected.
        let app_id: String = py_str_or_empty(&self.steam_app_id)
            .chars()
            .filter(char::is_ascii_digit)
            .collect();
        self.steam_app_id = serde_json::json!(take_chars(&app_id, 12));
        self.notes = serde_json::json!(take_chars(&py_str_or_empty(&self.notes), 500));

        // `dict(self.gpu_tuning or {})` - a falsy value is an empty dict, and
        // an empty list is a valid (empty) dict too. Anything else raises and
        // drops the profile.
        let pairs = py_dict(&self.gpu_tuning).ok_or("gpu_tuning is not a mapping")?;
        self.gpu_tuning = pairs
            .into_iter()
            .filter(|(k, _)| k.chars().count() < 40)
            .map(|(k, v)| (k, serde_json::json!(py_bool(&v))))
            .collect();
        Ok(())
    }

    /// The enabled runner and GPU-tuning toggles, as concrete env vars.
    pub fn env_assignments(&self) -> Vec<(String, String)> {
        let mut out: Vec<(String, String)> = Vec::new();
        let mut put = |name: &str, value: &str| match out.iter_mut().find(|(k, _)| k == name) {
            Some(slot) => slot.1 = value.to_string(),
            None => out.push((name.to_string(), value.to_string())),
        };
        for (key, value) in &self.runner_vars {
            if !py_bool(value) {
                continue;
            }
            if let Some((_, env)) = RUNNER_VARS.iter().find(|(name, _)| name == key) {
                for (var, val) in *env {
                    put(var, val);
                }
            }
        }
        // RADV_PERFTEST is a comma-list, so the values are collected and
        // joined rather than overwriting each other.
        let mut radv: Vec<&str> = Vec::new();
        for (_vendor, key, env) in GPU_TUNING_VARS {
            if !self.gpu_tuning.get(*key).is_some_and(py_bool) {
                continue;
            }
            for (var, val) in *env {
                if *var == "RADV_PERFTEST" {
                    if !radv.contains(val) {
                        radv.push(val);
                    }
                } else {
                    put(var, val);
                }
            }
        }
        if !radv.is_empty() {
            radv.sort_unstable();
            let joined = radv.join(",");
            put("RADV_PERFTEST", &joined);
        }
        // Deliberately NOT sorted: the Python builds a dict and the wrapper
        // writes it out in insertion order, so sorting here would change what
        // `--print-env-for` emits for no benefit.
        out
    }
}

/// `str(value)` for the values that reach it here.
fn py_str(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Null => "None".into(),
        serde_json::Value::Bool(b) => {
            if *b {
                "True".into()
            } else {
                "False".into()
            }
        }
        other => other.to_string(),
    }
}

fn clamped(value: &serde_json::Value, low: i64, high: i64) -> Result<serde_json::Value, String> {
    let raw = py_int(value).ok_or_else(|| format!("{value} is not an integer"))?;
    Ok(serde_json::json!(clamp_i64(raw, low, high)))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub schema_version: i64,
    pub master_enabled: serde_json::Value,
    pub poll_interval: serde_json::Value,
    pub diagnostics_enabled: serde_json::Value,
    pub diagnostics_sample_interval: serde_json::Value,
    pub llm_model_hint: serde_json::Value,
    pub auto_detect: serde_json::Value,
    pub ignored_games: serde_json::Value,
    pub prometheus_textfile: serde_json::Value,
    pub profiles: Vec<GameProfile>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            master_enabled: serde_json::json!(true),
            poll_interval: serde_json::json!(7),
            diagnostics_enabled: serde_json::json!(true),
            diagnostics_sample_interval: serde_json::json!(1.0),
            llm_model_hint: serde_json::json!(""),
            auto_detect: serde_json::json!(true),
            ignored_games: serde_json::json!([]),
            prometheus_textfile: serde_json::json!(""),
            profiles: Vec::new(),
        }
    }
}

impl Settings {
    pub fn normalise(&mut self) -> Result<(), String> {
        self.poll_interval = clamped(&self.poll_interval, 3, 30)?;
        Ok(())
    }

    pub fn profile_for_exe(&self, exe: &str) -> Option<&GameProfile> {
        self.profiles.iter().find(|p| p.exe == exe)
    }

    pub fn enabled_profiles(&self) -> Vec<&GameProfile> {
        if !py_bool(&self.master_enabled) {
            return Vec::new();
        }
        self.profiles
            .iter()
            .filter(|p| py_bool(&p.enabled))
            .collect()
    }
}

/// The settings a fresh install ships with: sane defaults for the two games
/// named in the brief.
///
/// This is what a file that cannot be read at all falls back to - not an
/// empty config. Somebody whose config went missing should still find the
/// tool doing something sensible.
pub fn default_settings() -> Settings {
    let mut wow = GameProfile {
        exe: "Wow.exe".into(),
        display_name: "World of Warcraft".into(),
        match_mode: serde_json::json!("exact"),
        ..Default::default()
    };
    let mut rs = GameProfile {
        exe: "rs2client".into(),
        display_name: "RuneScape (native)".into(),
        match_mode: serde_json::json!("substring"),
        per_game_mangohud: serde_json::json!(false),
        ..Default::default()
    };
    // Both are constructed from literals that normalise cleanly; an error
    // here would mean the shipped defaults are themselves invalid.
    let _ = wow.normalise();
    let _ = rs.normalise();
    Settings {
        profiles: vec![wow, rs],
        ..Default::default()
    }
}

/// Load settings from parsed JSON, the way `_from_dict` does.
///
/// A profile that would raise in the Python is dropped rather than failing the
/// whole load - a hand-broken entry should cost that one game's settings, not
/// the ability to start.
pub fn from_value(raw: &serde_json::Value) -> Settings {
    let Some(object) = raw.as_object() else {
        return default_settings();
    };
    let mut top = object.clone();
    top.remove("schema_version");
    let raw_profiles = top.remove("profiles").unwrap_or(serde_json::Value::Null);

    let mut settings: Settings =
        serde_json::from_value(serde_json::Value::Object(top)).unwrap_or_default();

    let mut profiles = Vec::new();
    for entry in raw_profiles.as_array().unwrap_or(&Vec::new()) {
        if !entry.is_object() {
            continue;
        }
        let Ok(mut profile) = serde_json::from_value::<GameProfile>(entry.clone()) else {
            continue;
        };
        if profile.normalise().is_ok() {
            profiles.push(profile);
        }
    }
    settings.profiles = profiles;
    // Settings.__post_init__ runs after the profiles are attached, and a
    // poll_interval that will not convert is fatal in the Python too - it
    // raises out of load() and the caller falls back to defaults.
    if settings.normalise().is_err() {
        // The Python rebuilds Settings() and keeps the profiles it already
        // parsed rather than discarding them with the global settings.
        let mut fallback = Settings {
            profiles: settings.profiles,
            ..Default::default()
        };
        let _ = fallback.normalise();
        return fallback;
    }
    settings
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile(json: serde_json::Value) -> Option<GameProfile> {
        let mut p: GameProfile = serde_json::from_value(json).ok()?;
        p.normalise().ok()?;
        Some(p)
    }

    #[test]
    fn a_profile_needs_only_an_exe() {
        let p = profile(serde_json::json!({"exe": "Wow.exe"})).expect("valid");
        assert_eq!(p.display_name, "Wow.exe", "the name defaults to the exe");
        assert!(py_bool(&p.enabled));
        assert_eq!(p.match_mode, "exact");
        assert_eq!(p.nice_value, serde_json::json!(-5));
    }

    #[test]
    fn an_exe_that_is_a_path_is_refused() {
        // The token is used as a regex and as a match target, never opened -
        // but a profile can arrive from an import or a community fetch, and a
        // path here is a sign something upstream is wrong.
        for bad in [
            "", "   ", ".", "..", "../x", "a/b", "a\\b", "a\0b", "a\nb", "\"\"",
        ] {
            assert!(
                profile(serde_json::json!({"exe": bad})).is_none(),
                "{bad:?} was accepted"
            );
        }
        assert!(profile(serde_json::json!({"exe": &"a".repeat(129)})).is_none());
        assert!(profile(serde_json::json!({"exe": &"a".repeat(128)})).is_some());
    }

    #[test]
    fn quotes_around_a_name_are_stripped() {
        let p = profile(serde_json::json!({"exe": "  \"Wow.exe\"  "})).expect("valid");
        assert_eq!(p.exe, "Wow.exe");
    }

    #[test]
    fn a_regex_exe_keeps_its_metacharacters() {
        // match_mode="regex" is a supported mode, so these are not corruption.
        let p = profile(serde_json::json!({"exe": "Wow.*\\.exe$", "match_mode": "regex"}));
        assert!(p.is_none(), "a backslash is still a path separator");
        let p =
            profile(serde_json::json!({"exe": "Wow.*exe$", "match_mode": "regex"})).expect("valid");
        assert_eq!(p.exe, "Wow.*exe$");
        assert_eq!(p.match_mode, "regex");
    }

    #[test]
    fn every_numeric_field_is_clamped() {
        let p = profile(serde_json::json!({
            "exe": "a", "nice_value": -50, "pl1_w": 9999, "pl2_w": -1,
            "battery_pl1_w": 9999, "battery_pl2_w": -5,
            "refresh_rate_hz": 99999, "fps_dip_floor": 1, "fps_dip_ratio": 5.0,
        }))
        .expect("valid");
        assert_eq!(p.nice_value, serde_json::json!(-10));
        assert_eq!(p.pl1_w, serde_json::json!(500));
        assert_eq!(p.pl2_w, serde_json::json!(0));
        assert_eq!(p.battery_pl1_w, serde_json::json!(500));
        assert_eq!(p.battery_pl2_w, serde_json::json!(0));
        assert_eq!(p.refresh_rate_hz, serde_json::json!(1000));
        assert_eq!(p.fps_dip_floor, serde_json::json!(5));
        assert_eq!(p.fps_dip_ratio, serde_json::json!(0.9));
    }

    #[test]
    fn a_float_where_an_int_belongs_truncates_toward_zero() {
        // Python's int(). A hand-edited config is the way this arrives.
        let p = profile(serde_json::json!({"exe": "a", "nice_value": -5.9})).expect("valid");
        assert_eq!(p.nice_value, serde_json::json!(-5));
        let p = profile(serde_json::json!({"exe": "a", "pl1_w": 45.9})).expect("valid");
        assert_eq!(p.pl1_w, serde_json::json!(45));
    }

    #[test]
    fn a_quoted_number_is_still_a_number() {
        // int("-5") is -5 in Python, and a hand-edited config quotes numbers
        // constantly. Rejecting these would drop profiles the Python keeps.
        let p = profile(serde_json::json!({"exe": "a", "nice_value": "-5"})).expect("valid");
        assert_eq!(p.nice_value, serde_json::json!(-5));
        let p = profile(serde_json::json!({"exe": "a", "pl1_w": " 45 "})).expect("valid");
        assert_eq!(p.pl1_w, serde_json::json!(45));
    }

    #[test]
    fn a_string_that_is_not_a_number_drops_the_profile() {
        // Rather than half-applying it. A machine tuned by a file the user
        // cannot read is worse than a game with no profile.
        assert!(profile(serde_json::json!({"exe": "a", "pl1_w": "lots"})).is_none());
        assert!(profile(serde_json::json!({"exe": "a", "nice_value": ""})).is_none());
        // int("5.5") raises in Python too - this is the integer grammar, not
        // a float parse with a truncation after it.
        assert!(profile(serde_json::json!({"exe": "a", "nice_value": "5.5"})).is_none());
    }

    #[test]
    fn unknown_keys_are_dropped_exactly_as_python_drops_them() {
        // Not preserved. This is a real property of the format - a key from a
        // newer build does not survive an older build saving the file - and
        // the port reproduces it rather than improving on it unilaterally.
        let p = profile(serde_json::json!({"exe": "a", "a_key_from_2027": 5})).expect("valid");
        let round = serde_json::to_value(&p).expect("serialises");
        assert!(round.get("a_key_from_2027").is_none());
    }

    #[test]
    fn missing_toggle_keys_are_filled_in_without_overwriting() {
        let p = profile(serde_json::json!({
            "exe": "a", "mangohud": {"fps": false}, "runner_vars": {"nvapi": false},
        }))
        .expect("valid");
        assert_eq!(p.mangohud["fps"], serde_json::json!(false), "kept");
        assert_eq!(p.mangohud["cpu_temp"], serde_json::json!(true), "filled");
        assert_eq!(p.runner_vars["nvapi"], serde_json::json!(false), "kept");
        assert_eq!(p.runner_vars["fsync"], serde_json::json!(true), "filled");
    }

    #[test]
    fn an_invalid_scheduler_name_is_dropped_not_fatal() {
        let p = profile(serde_json::json!({"exe": "a", "scx_scheduler": "../etc"}))
            .expect("the profile survives");
        assert_eq!(p.scx_scheduler, "", "the name does not");
        let p =
            profile(serde_json::json!({"exe": "a", "scx_scheduler": "scx_lavd"})).expect("valid");
        assert_eq!(p.scx_scheduler, "lavd", "stored short");
    }

    #[test]
    fn an_unknown_enum_value_falls_back_rather_than_failing() {
        let p = profile(serde_json::json!({
            "exe": "a", "match_mode": "fuzzy", "core_pin": "all", "scx_mode": "turbo",
            "gamescope": {"upscale": "dlss"},
        }))
        .expect("valid");
        assert_eq!(p.match_mode, "exact");
        assert_eq!(p.core_pin, "off");
        assert_eq!(p.scx_mode, "gaming");
        assert_eq!(p.gamescope["upscale"], serde_json::json!("off"));
    }

    #[test]
    fn a_steam_app_id_keeps_only_its_digits() {
        let p = profile(serde_json::json!({"exe": "a", "steam_app_id": "app #12345 (wow)"}))
            .expect("valid");
        assert_eq!(p.steam_app_id, serde_json::json!("12345"));
        let p = profile(serde_json::json!({"exe": "a", "steam_app_id": 12345})).expect("valid");
        assert_eq!(p.steam_app_id, serde_json::json!("12345"));
    }

    #[test]
    fn long_text_is_truncated_by_characters_not_bytes() {
        // A name in Cyrillic is 200 characters, not 200 bytes, and a byte cut
        // could split a character in half.
        let name = "\u{44f}".repeat(300);
        let p = profile(serde_json::json!({"exe": "a", "display_name": name})).expect("valid");
        assert_eq!(p.display_name.chars().count(), 200);
        let notes = "\u{44f}".repeat(900);
        let p = profile(serde_json::json!({"exe": "a", "notes": notes})).expect("valid");
        assert_eq!(py_str(&p.notes).chars().count(), 500);
    }

    #[test]
    fn vrr_outputs_are_bounded_in_both_directions() {
        let many: Vec<String> = (0..40).map(|i| format!("DP-{i}")).collect();
        let p = profile(serde_json::json!({"exe": "a", "vrr_outputs": many})).expect("valid");
        assert_eq!(p.vrr_outputs.as_array().expect("a list").len(), 16);
        let long = "x".repeat(200);
        let p = profile(serde_json::json!({"exe": "a", "vrr_outputs": [long]})).expect("valid");
        assert_eq!(py_str(&p.vrr_outputs[0]).chars().count(), 64);
    }

    #[test]
    fn radv_values_are_joined_not_overwritten() {
        // Three separate RADV_PERFTEST assignments have to become one
        // comma-list, or turning on two of them silently keeps only the last.
        let p = profile(serde_json::json!({
            "exe": "a",
            "gpu_tuning": {"radv_gpl": true, "radv_nggc": true, "radv_rt": true},
            "runner_vars": {"nvapi": false, "fsync": false},
        }))
        .expect("valid");
        let env = p.env_assignments();
        let radv = env.iter().find(|(k, _)| k == "RADV_PERFTEST").expect("set");
        assert_eq!(radv.1, "gpl,nggc,rt");
    }

    #[test]
    fn a_corrupt_profile_does_not_take_the_rest_with_it() {
        let settings = from_value(&serde_json::json!({
            "profiles": [
                {"exe": "good.exe"},
                {"exe": "../bad"},
                "not even an object",
                {"exe": "also-good", "nice_value": "text"},
                {"exe": "third.exe"},
            ]
        }));
        let names: Vec<&str> = settings.profiles.iter().map(|p| p.exe.as_str()).collect();
        assert_eq!(names, ["good.exe", "third.exe"]);
    }

    #[test]
    fn a_file_that_is_not_an_object_gives_defaults() {
        assert_eq!(
            from_value(&serde_json::json!([])).poll_interval,
            serde_json::json!(7)
        );
        assert_eq!(from_value(&serde_json::json!("nope")).master_enabled, true);
    }

    #[test]
    fn the_schema_version_in_the_file_is_ignored() {
        // It is stripped on load and rewritten on save, so a file claiming to
        // be from the future is read as the current schema rather than
        // rejected - which is what makes a downgrade survivable.
        let s = from_value(&serde_json::json!({"schema_version": 99, "poll_interval": 9}));
        assert_eq!(s.schema_version, SCHEMA_VERSION);
        assert_eq!(s.poll_interval, serde_json::json!(9));
    }

    #[test]
    fn slugs_are_filesystem_safe_and_never_empty() {
        assert_eq!(slug("World of Warcraft"), "World_of_Warcraft");
        assert_eq!(slug("../../etc/passwd"), "etc_passwd");
        assert_eq!(slug("!!!"), "game");
        assert_eq!(slug(""), "game");
        assert_eq!(slug(&"a".repeat(200)).chars().count(), 80);
    }
}
