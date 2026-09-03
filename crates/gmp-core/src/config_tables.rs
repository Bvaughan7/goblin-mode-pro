//! Tables a profile is normalised against.
//!
//! GENERATED from `src/goblinmode/config.py` by
//! `tools/gen_config_tables.py`. Do not edit by hand - a typo here
//! silently changes what a user's saved settings mean.

/// A set of environment assignments: `(name, value)` pairs.
pub type EnvSet = &'static [(&'static str, &'static str)];

/// Runner-variable toggle -> the env assignments it applies when on.
pub const RUNNER_VARS: &[(&str, EnvSet)] = &[
    (
        "nvapi",
        &[("PROTON_ENABLE_NVAPI", "1"), ("DXVK_ENABLE_NVAPI", "1")],
    ),
    ("fsync", &[("WINEFSYNC", "1")]),
    ("no_esync", &[("PROTON_NO_ESYNC", "1")]),
    ("dxvk_async", &[("DXVK_ASYNC", "1")]),
];

/// Vendor GPU-driver tuning: (vendor, key, env assignments).
/// The label is not carried - it is GUI text and reaches no decision.
pub const GPU_TUNING_VARS: &[(&str, &str, EnvSet)] = &[
    (
        "nvidia",
        "threaded_gl",
        &[("__GL_THREADED_OPTIMIZATIONS", "1")],
    ),
    (
        "nvidia",
        "shader_cache",
        &[
            ("__GL_SHADER_DISK_CACHE", "1"),
            ("__GL_SHADER_DISK_CACHE_SKIP_CLEANUP", "1"),
        ],
    ),
    (
        "nvidia",
        "force_gsync",
        &[("__GL_GSYNC_ALLOWED", "1"), ("__GL_VRR_ALLOWED", "1")],
    ),
    ("nvidia", "max_fps_none", &[("__GL_SYNC_TO_VBLANK", "0")]),
    ("amd", "glthread", &[("mesa_glthread", "true")]),
    ("amd", "radv_gpl", &[("RADV_PERFTEST", "gpl")]),
    ("amd", "radv_nggc", &[("RADV_PERFTEST", "nggc")]),
    ("amd", "radv_rt", &[("RADV_PERFTEST", "rt")]),
    ("intel", "anv_gpl", &[("ANV_GPL", "true")]),
    ("intel", "glthread", &[("mesa_glthread", "true")]),
];

pub const MATCH_MODES: &[&str] = &["exact", "substring", "regex"];
pub const CORE_PIN_MODES: &[&str] = &["off", "performance", "cache0"];
pub const SCX_MODES: &[&str] = &["auto", "gaming", "lowlatency", "powersave", "server"];
pub const GAMESCOPE_UPSCALERS: &[&str] = &["off", "fsr", "nis", "integer"];

/// MangoHud toggle defaults, in the order the Python builds them.
pub const DEFAULT_MANGOHUD: &[(&str, bool)] = &[
    ("enabled", false),
    ("fps", true),
    ("cpu_temp", true),
    ("gpu_temp", true),
    ("ram", false),
    ("frame_timing", false),
];

pub const DEFAULT_RUNNER_VARS: &[(&str, bool)] = &[
    ("nvapi", true),
    ("fsync", true),
    ("no_esync", false),
    ("dxvk_async", false),
];

/// gamescope defaults. Mixed types, so each is spelled out.
pub fn default_gamescope() -> serde_json::Map<String, serde_json::Value> {
    let mut map = serde_json::Map::new();
    map.insert("w".to_string(), serde_json::json!(0));
    map.insert("h".to_string(), serde_json::json!(0));
    map.insert("refresh".to_string(), serde_json::json!(0));
    map.insert("upscale".to_string(), serde_json::json!("off"));
    map.insert("hdr".to_string(), serde_json::json!(false));
    map.insert("borderless".to_string(), serde_json::json!(true));
    map.insert("steam_overlay".to_string(), serde_json::json!(true));
    map
}
