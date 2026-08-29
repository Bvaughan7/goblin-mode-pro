#!/usr/bin/env bash
#
# Goblin Mode Pro installer.
#
#   ./install.sh              full install (includes the root helper; asks for sudo)
#   ./install.sh --user       skip the root helper (governor/power tuning disabled)
#   ./install.sh --uninstall  remove everything this script installed
#
# Works on any systemd Linux with polkit. It installs the Python packages for
# your distribution when it knows how, and otherwise tells you exactly what to
# install by hand.
#
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="/usr"
LIB_DIR="/usr/lib/goblin-mode-pro"
MODE="${1:-}"

msg()  { printf '\033[1;32m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1; }

# --------------------------------------------------------------------------
# dependencies
# --------------------------------------------------------------------------
detect_pm() {
    for pm in pacman apt-get dnf zypper xbps-install eopkg; do
        need_cmd "$pm" && { echo "$pm"; return; }
    done
    echo ""
}

install_deps() {
    local pm; pm="$(detect_pm)"
    msg "Package manager: ${pm:-unknown}"
    case "$pm" in
        pacman)
            sudo pacman -S --needed -- python python-gobject python-psutil \
                python-pillow python-pystray gtk4 libadwaita || true
            sudo pacman -S --needed --asdeps -- wl-clipboard mangohud gamemode gamescope || true
            ;;
        apt-get)
            sudo apt-get update || true
            sudo apt-get install -y python3 python3-gi python3-gi-cairo python3-psutil \
                python3-pil gir1.2-gtk-4.0 gir1.2-adw-1 python3-pip \
                wl-clipboard mangohud gamemode gamescope || true
            need_cmd pip3 && pip3 install --user --break-system-packages pystray 2>/dev/null || true
            ;;
        dnf)
            sudo dnf install -y python3 python3-gobject python3-psutil python3-pillow \
                python3-pystray gtk4 libadwaita wl-clipboard mangohud gamemode gamescope || true
            ;;
        zypper)
            sudo zypper install -y python3 python3-gobject python3-psutil python3-Pillow \
                python3-pystray gtk4-tools libadwaita wl-clipboard mangohud gamemode gamescope || true
            ;;
        *)
            warn "Unknown package manager - install the dependencies manually (see below)."
            ;;
    esac
}

check_deps() {
    local missing=()
    python3 - <<'PY' || missing+=("the checks above")
import sys
def probe(mod, extra=None):
    try:
        __import__(mod)
        if extra:
            import gi
            for ns, ver in extra:
                gi.require_version(ns, ver)
                __import__("gi.repository", fromlist=[ns])
        return True
    except Exception as e:                       # noqa
        print(f"  missing: {mod}  ({e})")
        return False

ok = True
ok &= probe("gi")
ok &= probe("gi", [("Gtk", "4.0"), ("Adw", "1")])
ok &= probe("psutil")
if not probe("pystray"):
    print("  (pystray is optional - only the tray icon needs it)")
sys.exit(0 if ok else 1)
PY
    if ((${#missing[@]})); then
        warn "Some required Python modules are missing. Install them with your"
        warn "package manager (names vary): python3-gi / python-gobject, GTK4 +"
        warn "libadwaita GObject introspection, python3-psutil, and optionally"
        warn "python3-pystray for the tray icon. Then re-run this script."
        die  "dependencies incomplete"
    fi
}

# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------
install_package() {
    msg "Installing the application to $LIB_DIR"
    sudo rm -rf -- "$LIB_DIR/goblinmode"
    sudo install -d -m0755 -- "$LIB_DIR"
    sudo cp -rT -- "$REPO_DIR/src/goblinmode" "$LIB_DIR/goblinmode"
    sudo python3 -m compileall -q -- "$LIB_DIR/goblinmode" || true
    _shim /usr/bin/goblin-mode-pro-daemon goblinmode.daemon
    _shim /usr/bin/goblin-mode-pro        goblinmode.gui.app
    _shim /usr/bin/goblin-mode-pro-cli    goblinmode.cli
}

_shim() {
    sudo tee "$1" >/dev/null <<EOF
#!/usr/bin/python3
import sys
sys.path.insert(0, "$LIB_DIR")
from $2 import main
raise SystemExit(main())
EOF
    sudo chmod 0755 -- "$1"
}

install_helper() {
    msg "Installing the privileged helper, polkit action, D-Bus policy and system unit"
    sudo install -Dm0755 "$REPO_DIR/helper/goblin_helper.py" "$LIB_DIR/goblin_helper.py"
    sudo install -Dm0644 "$REPO_DIR/data/polkit/com.goblinmode.pro.policy" \
        "$PREFIX/share/polkit-1/actions/com.goblinmode.pro.policy"
    sudo install -Dm0644 "$REPO_DIR/data/dbus/com.goblinmode.ProHelper.conf" \
        "$PREFIX/share/dbus-1/system.d/com.goblinmode.ProHelper.conf"
    sudo install -Dm0644 "$REPO_DIR/data/systemd/goblin-mode-pro-helper.service" \
        "$PREFIX/lib/systemd/system/goblin-mode-pro-helper.service"

    # AMD laptop TDP control needs a looser sandbox for ryzenadj; only apply the
    # drop-in when ryzenadj is actually installed.
    local ddir="/etc/systemd/system/goblin-mode-pro-helper.service.d"
    if need_cmd ryzenadj; then
        msg "ryzenadj found - enabling AMD TDP control (looser helper sandbox)"
        sudo install -Dm0644 "$REPO_DIR/data/systemd/helper-amd-tdp.conf" "$ddir/amd-tdp.conf"
    else
        sudo rm -f -- "$ddir/amd-tdp.conf"
    fi
    if need_cmd intel-undervolt; then
        msg "intel-undervolt found - enabling undervolt re-apply (looser helper sandbox)"
        sudo install -Dm0644 "$REPO_DIR/data/systemd/helper-undervolt.conf" "$ddir/undervolt.conf"
    else
        sudo rm -f -- "$ddir/undervolt.conf"
    fi

    sudo systemctl daemon-reload
    sudo systemctl enable --now goblin-mode-pro-helper.service
}

install_user_bits() {
    msg "Installing the desktop entry, icon and user service"
    sudo install -Dm0644 "$REPO_DIR/data/com.goblinmode.Pro.desktop" \
        "$PREFIX/share/applications/com.goblinmode.Pro.desktop"
    sudo install -Dm0644 "$REPO_DIR/data/icons/com.goblinmode.Pro.svg" \
        "$PREFIX/share/icons/hicolor/scalable/apps/com.goblinmode.Pro.svg"
    sudo install -Dm0644 "$REPO_DIR/data/systemd/goblin-mode-pro.service" \
        "$PREFIX/lib/systemd/user/goblin-mode-pro.service"
    sudo gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" 2>/dev/null || true

    # translation catalogues, if any and if msgfmt is around
    if need_cmd msgfmt; then
        for po in "$REPO_DIR"/po/*.po; do
            [ -e "$po" ] || continue
            local lang; lang="$(basename "$po" .po)"
            sudo install -d "$PREFIX/share/locale/$lang/LC_MESSAGES"
            sudo msgfmt "$po" -o "$PREFIX/share/locale/$lang/LC_MESSAGES/goblin-mode-pro.mo"
        done
    fi

    systemctl --user daemon-reload
    systemctl --user enable --now goblin-mode-pro.service
    goblin-mode-pro-daemon --write-wrapper >/dev/null
}

uninstall() {
    warn "Removing Goblin Mode Pro"
    systemctl --user disable --now goblin-mode-pro.service 2>/dev/null || true
    sudo systemctl disable --now goblin-mode-pro-helper.service 2>/dev/null || true
    sudo rm -f -- \
        "$PREFIX/share/polkit-1/actions/com.goblinmode.pro.policy" \
        "$PREFIX/share/dbus-1/system.d/com.goblinmode.ProHelper.conf" \
        "$PREFIX/lib/systemd/system/goblin-mode-pro-helper.service" \
        "$PREFIX/lib/systemd/user/goblin-mode-pro.service" \
        "$PREFIX/share/applications/com.goblinmode.Pro.desktop" \
        "$PREFIX/share/icons/hicolor/scalable/apps/com.goblinmode.Pro.svg" \
        /usr/bin/goblin-mode-pro-daemon /usr/bin/goblin-mode-pro /usr/bin/goblin-mode-pro-cli
    sudo rm -rf -- "$LIB_DIR" \
        /etc/systemd/system/goblin-mode-pro-helper.service.d
    sudo systemctl daemon-reload
    systemctl --user daemon-reload
    rm -f -- "$HOME/.local/bin/goblin-run"
    msg "Removed. Your settings in ~/.config/goblin-mode-pro were left alone."
}

case "$MODE" in
    --uninstall) uninstall ;;
    --user)
        need_cmd systemctl || die "this installer needs systemd"
        install_deps; check_deps; install_package; install_user_bits
        warn "Skipped the root helper: CPU speed and power tuning are turned off."
        msg  "Everything else (game detection, overlay, diagnostics, checks) works."
        ;;
    "" | --all)
        need_cmd systemctl || die "this installer needs systemd"
        need_cmd pkaction || warn "polkit doesn't seem to be installed - the helper needs it."
        install_deps; check_deps; install_package; install_helper; install_user_bits
        msg "Installed. Open 'Goblin Mode Pro' from your application menu."
        msg "For games launched through Steam, set the launch options to:  goblin-run %command%"
        ;;
    -h | --help) sed -n '2,13p' "$0" ;;
    *) warn "unknown option: $MODE"; sed -n '2,13p' "$0"; exit 2 ;;
esac
