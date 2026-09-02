//! nvidia-drm.modeset.
//!
//! There is no runtime write path for this parameter - it is a boot-time
//! modprobe option, so the only thing to do is write a modprobe.d drop-in and
//! wait for a reboot (plus an initramfs regen on distros that bake modprobe.d
//! into it).

use std::path::{Path, PathBuf};

/// The drop-in this helper owns. Its ENTIRE content is one of two fixed
/// strings; nothing else is ever written to it, which is what keeps a
/// caller-supplied value from reaching a file that the kernel reads at boot.
pub const NVIDIA_MODESET_CONF: &str = "/etc/modprobe.d/goblin-mode-pro-nvidia.conf";

pub fn conf_path() -> PathBuf {
    PathBuf::from(NVIDIA_MODESET_CONF)
}

/// Write the drop-in. Takes effect after a reboot.
pub fn set_modeset(conf: &Path, enabled: bool) -> bool {
    let text = format!("options nvidia_drm modeset={}\n", u8::from(enabled));
    match write_readable(conf, &text) {
        Ok(()) => {
            tracing::info!(
                "wrote {}: modeset={} (takes effect after reboot)",
                conf.display(),
                u8::from(enabled)
            );
            true
        }
        Err(err) => {
            tracing::warn!("could not write {}: {err}", conf.display());
            false
        }
    }
}

/// Write the file and force it to 0644.
///
/// The unit runs with `UMask=0077`, which is right for everything the helper
/// puts in /run and WRONG here: this is a config file in /etc that initramfs
/// tooling and the user both need to read. Every other file in modprobe.d is
/// 0644, and a root-only one is both surprising and invisible to anyone trying
/// to work out why modeset is set the way it is. This was a real bug, fixed
/// in v1.3.1 after it shipped 0600.
fn write_readable(conf: &Path, text: &str) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    if let Some(parent) = conf.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(conf, text)?;
    std::fs::set_permissions(conf, std::fs::Permissions::from_mode(0o644))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("gmp-nvidia-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir.join("modprobe.d").join("goblin-mode-pro-nvidia.conf")
    }

    #[test]
    fn the_file_holds_exactly_one_of_two_fixed_strings() {
        let conf = scratch("content");
        assert!(set_modeset(&conf, true));
        assert_eq!(
            std::fs::read_to_string(&conf).unwrap(),
            "options nvidia_drm modeset=1\n"
        );
        assert!(set_modeset(&conf, false));
        assert_eq!(
            std::fs::read_to_string(&conf).unwrap(),
            "options nvidia_drm modeset=0\n"
        );
        let _ = std::fs::remove_dir_all(conf.parent().unwrap().parent().unwrap());
    }

    #[test]
    fn the_drop_in_is_world_readable() {
        // v1.3.1: it shipped 0600 because the unit's UMask=0077 is right for
        // /run and wrong for /etc. initramfs tooling has to be able to read it.
        let conf = scratch("mode");
        assert!(set_modeset(&conf, true));
        let mode = std::fs::metadata(&conf).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o644, "got {mode:o}");
        let _ = std::fs::remove_dir_all(conf.parent().unwrap().parent().unwrap());
    }

    #[test]
    fn a_rewrite_corrects_the_mode_of_an_existing_file() {
        // A file left 0600 by a pre-1.3.1 helper is fixed on the next write,
        // not left as found.
        let conf = scratch("rewrite");
        set_modeset(&conf, true);
        std::fs::set_permissions(&conf, std::fs::Permissions::from_mode(0o600)).unwrap();
        assert!(set_modeset(&conf, true));
        assert_eq!(
            std::fs::metadata(&conf).unwrap().permissions().mode() & 0o777,
            0o644
        );
        let _ = std::fs::remove_dir_all(conf.parent().unwrap().parent().unwrap());
    }

    #[test]
    fn an_unwritable_location_is_false_not_a_panic() {
        assert!(!set_modeset(Path::new("/proc/nonexistent/gmp.conf"), true));
    }
}
