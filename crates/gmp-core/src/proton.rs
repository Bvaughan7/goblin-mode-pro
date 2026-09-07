//! Proton/Wine build discovery and shader-cache accounting.
//!
//! Read-only helpers for the GUI: which custom builds are installed, and how
//! much disk the shader caches are using. The walking of the filesystem stays
//! in Python; what is here is what the walk's results MEAN - which directories
//! count as a build, which of two entries with the same name wins, and which
//! paths a "clear this cache" request is allowed to name.

use serde_json::{Map, Value};

/// One directory that might be a Proton or Wine build.
#[derive(Debug, Clone, PartialEq)]
pub struct Candidate {
    pub name: String,
    pub path: String,
    /// A `proton` launcher script at the top level.
    pub has_proton: bool,
    /// A `bin/wine` binary.
    pub has_bin_wine: bool,
    /// A `version` file.
    pub has_version: bool,
    pub mtime: f64,
}

/// What kind of build a directory holds, or nothing if it is not one.
///
/// The order is a precedence and not a formality: a build carrying BOTH a
/// `proton` launcher and a `bin/wine` is Proton, because Proton ships a Wine
/// inside it and the launcher is the thing that will actually be run. Testing
/// for Wine first would label every Proton build as Wine.
///
/// A bare `version` file is the third chance, and it answers Proton: it is
/// what a Proton build that has been stripped down still carries.
pub fn build_kind(candidate: &Candidate) -> Option<&'static str> {
    if candidate.has_proton {
        Some("Proton")
    } else if candidate.has_bin_wine {
        Some("Wine")
    } else if candidate.has_version {
        Some("Proton")
    } else {
        None
    }
}

/// The installed builds, newest first.
///
/// Candidates are expected in the order the directories were walked, and the
/// FIRST entry of a given name wins: a build installed in two compatibility
/// directories is one build, and the one found first is the one the launcher
/// would use.
///
/// The sort is stable and descending, which is what Python's
/// `sorted(reverse=True)` does - two builds sharing a timestamp keep the order
/// they were found in rather than being flipped.
pub fn installed_builds(candidates: &[Candidate]) -> Vec<Value> {
    let mut seen: Vec<&Candidate> = Vec::new();
    for candidate in candidates {
        if build_kind(candidate).is_none() {
            continue;
        }
        if seen.iter().any(|kept| kept.name == candidate.name) {
            continue;
        }
        seen.push(candidate);
    }
    seen.sort_by(|a, b| b.mtime.total_cmp(&a.mtime));
    seen.iter()
        .map(|candidate| {
            let mut row = Map::new();
            row.insert("name".into(), candidate.name.clone().into());
            row.insert(
                "kind".into(),
                build_kind(candidate).expect("filtered above").into(),
            );
            row.insert("path".into(), candidate.path.clone().into());
            row.insert("mtime".into(), candidate.mtime.into());
            Value::Object(row)
        })
        .collect()
}

/// One shader-cache directory that exists.
#[derive(Debug, Clone, PartialEq)]
pub struct CacheDir {
    pub label: String,
    /// The path as it was listed, which is what a caller quotes back.
    pub path: String,
    /// The same directory with its symlinks resolved.
    pub real: String,
    pub bytes: i64,
}

/// The shader caches, biggest first.
///
/// Deduplicated by RESOLVED path rather than by listed path: a Steam library
/// reached through a symlink and the same library reached directly are one
/// directory, and counting it twice would double the number a person is being
/// asked to act on. The first spelling found is the one reported back, because
/// that is the one they will recognise.
pub fn shader_caches(dirs: &[CacheDir]) -> Vec<Value> {
    let mut seen: Vec<&CacheDir> = Vec::new();
    for dir in dirs {
        if seen.iter().any(|kept| kept.real == dir.real) {
            continue;
        }
        seen.push(dir);
    }
    // Stable and descending: equal sizes keep the order they were found
    // in, which is what Python's `sorted(reverse=True)` does.
    seen.sort_by_key(|dir| std::cmp::Reverse(dir.bytes));
    seen.iter()
        .map(|dir| {
            let mut row = Map::new();
            row.insert("label".into(), dir.label.clone().into());
            row.insert("path".into(), dir.path.clone().into());
            row.insert("bytes".into(), dir.bytes.into());
            Value::Object(row)
        })
        .collect()
}

/// Whether a "clear this cache" request names a directory we listed.
///
/// An allowlist drawn from the answer this module just gave, not a pattern:
/// the caller is about to delete the contents of whatever it names, and the
/// only paths that should be reachable are the ones already shown to the user.
/// It is compared against the LISTED spelling, so a resolved path that was
/// never displayed is refused.
pub fn is_known_cache(path: &str, listed: &[Value]) -> bool {
    listed
        .iter()
        .filter_map(|row| row.get("path").and_then(Value::as_str))
        .any(|known| known == path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(name: &str, mtime: f64) -> Candidate {
        Candidate {
            name: name.to_string(),
            path: format!("/compat/{name}"),
            has_proton: true,
            has_bin_wine: false,
            has_version: false,
            mtime,
        }
    }

    #[test]
    fn a_directory_with_none_of_the_three_markers_is_not_a_build() {
        let plain = Candidate {
            has_proton: false,
            has_bin_wine: false,
            has_version: false,
            ..candidate("notes", 1.0)
        };
        assert_eq!(build_kind(&plain), None);
        assert!(installed_builds(&[plain]).is_empty());
    }

    #[test]
    fn a_proton_launcher_wins_over_a_wine_inside_it() {
        // Proton ships a Wine. Testing for Wine first would label every
        // Proton build as Wine.
        let both = Candidate {
            has_proton: true,
            has_bin_wine: true,
            ..candidate("GE-Proton9-1", 1.0)
        };
        assert_eq!(build_kind(&both), Some("Proton"));
    }

    #[test]
    fn a_bare_wine_build_is_wine() {
        let wine = Candidate {
            has_proton: false,
            has_bin_wine: true,
            ..candidate("wine-tkg", 1.0)
        };
        assert_eq!(build_kind(&wine), Some("Wine"));
    }

    #[test]
    fn a_version_file_alone_is_a_stripped_proton() {
        let stripped = Candidate {
            has_proton: false,
            has_bin_wine: false,
            has_version: true,
            ..candidate("proton-lite", 1.0)
        };
        assert_eq!(build_kind(&stripped), Some("Proton"));
    }

    #[test]
    fn builds_come_back_newest_first() {
        let builds = installed_builds(&[
            candidate("old", 1.0),
            candidate("new", 3.0),
            candidate("mid", 2.0),
        ]);
        let names: Vec<&str> = builds.iter().map(|b| b["name"].as_str().unwrap()).collect();
        assert_eq!(names, vec!["new", "mid", "old"]);
    }

    #[test]
    fn the_same_name_in_two_places_is_one_build() {
        let mut second = candidate("GE-Proton9-1", 99.0);
        second.path = "/other/GE-Proton9-1".to_string();
        let builds = installed_builds(&[candidate("GE-Proton9-1", 1.0), second]);
        assert_eq!(builds.len(), 1);
        assert_eq!(
            builds[0]["path"], "/compat/GE-Proton9-1",
            "the one found first is the one the launcher would use"
        );
    }

    fn cache(label: &str, path: &str, real: &str, bytes: i64) -> CacheDir {
        CacheDir {
            label: label.to_string(),
            path: path.to_string(),
            real: real.to_string(),
            bytes,
        }
    }

    #[test]
    fn caches_come_back_biggest_first() {
        let rows = shader_caches(&[
            cache("DXVK", "/a", "/a", 10),
            cache("Steam", "/b", "/b", 500),
            cache("VKD3D", "/c", "/c", 100),
        ]);
        let labels: Vec<&str> = rows.iter().map(|r| r["label"].as_str().unwrap()).collect();
        assert_eq!(labels, vec!["Steam", "VKD3D", "DXVK"]);
    }

    #[test]
    fn one_directory_reached_two_ways_is_counted_once() {
        let rows = shader_caches(&[
            cache(
                "Steam",
                "/home/x/.steam/steam/shader",
                "/mnt/games/shader",
                500,
            ),
            cache("Steam", "/mnt/games/shader", "/mnt/games/shader", 500),
        ]);
        assert_eq!(rows.len(), 1);
        assert_eq!(
            rows[0]["path"], "/home/x/.steam/steam/shader",
            "reported by the spelling the user would recognise"
        );
    }

    #[test]
    fn only_a_path_that_was_listed_may_be_cleared() {
        let listed = shader_caches(&[cache("DXVK", "/a", "/a", 10)]);
        assert!(is_known_cache("/a", &listed));
        assert!(!is_known_cache("/b", &listed));
        assert!(!is_known_cache("", &listed));
        assert!(!is_known_cache("/a/..", &listed));
        assert!(!is_known_cache("/home/x", &listed));
    }
}
