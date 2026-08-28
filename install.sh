#!/usr/bin/env bash
#
# Goblin Mode Pro installer for Arch-based distributions.
#
#   ./install.sh              full install (root helper included; prompts for sudo)
#   ./install.sh --user       skip the root helper (limited mode)
#   ./install.sh --uninstall  remove everything this script installed
#
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="/usr"
LIB_DIR="/usr/lib/goblin-mode-pro"          # our own tree; never the distro's site-packages
MODE="${1:-}"

REQUIRED_PKGS=(python python-gobject python-psutil python-pillow python-pystray gtk4 libadwaita)
RECOMMENDED_PKGS=(python-pystray wl-clipboard mangohud gamemode)

msg()  { printf '\033[1;32m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m::\033[0m %s\n' "$*" >&2; }

install_deps() {
    msg "Installing required packages"
    sudo pacman -S --needed -- "${REQUIRED_PKGS[@]}"
    msg "Installing recommended packages (frame-rate watchdog, clipboard, gamemode)"
    sudo pacman -S --needed --asdeps -- "${RECOMMENDED_PKGS[@]}" || \
        warn "Some recommended packages were not installed; related features stay disabled."
}

install_package() {
    msg "Installing the application to $LIB_DIR"
    sudo rm -rf -- "$LIB_DIR/goblinmode"
    sudo install -d -m0755 -- "$LIB_DIR"
    sudo cp -rT -- "$REPO_DIR/src/goblinmode" "$LIB_DIR/goblinmode"
    sudo python3 -m compileall -q -- "$LIB_DIR/goblinmode" || true

    _shim /usr/bin/goblin-mode-pro-daemon goblinmode.daemon
    _shim /usr/bin/goblin-mode-pro        goblinmode.gui.app
}

_shim() {
    local path="$1" module="$2"
    sudo tee "$path" >/dev/null <<EOF
#!/usr/bin/python3
import sys
sys.path.insert(0, "$LIB_DIR")
from $module import main
raise SystemExit(main())
EOF
    sudo chmod 0755 -- "$path"
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
        /usr/bin/goblin-mode-pro-daemon /usr/bin/goblin-mode-pro
    sudo rm -rf -- "$LIB_DIR"
    sudo systemctl daemon-reload
    systemctl --user daemon-reload
    rm -f -- "$HOME/.local/bin/goblin-run"
    msg "Done. Configuration under ~/.config/goblin-mode-pro was left in place."
}

case "$MODE" in
    --uninstall)
        uninstall
        ;;
    --user)
        install_deps
        install_package
        install_user_bits
        warn "Skipped the root helper: CPU governor, renice and power limits are unavailable."
        ;;
    "" | --all)
        install_deps
        install_package
        install_helper
        install_user_bits
        msg "Installed. Launch 'goblin-mode-pro' or use the application menu."
        msg "For Proton games, set the launch options to:  goblin-run %command%"
        ;;
    -h | --help)
        sed -n '2,8p' "$0"
        ;;
    *)
        warn "unknown option: $MODE"
        sed -n '2,8p' "$0"
        exit 2
        ;;
esac
