#!/usr/bin/env bash
# Goblin Mode Pro installer for CachyOS / Arch.
#
#   ./install.sh            # system-wide install (uses sudo for the root bits)
#   ./install.sh --user     # everything except the root helper (limited mode)
#   ./install.sh --uninstall
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="/usr"
HELPER_DIR="/usr/lib/goblin-mode-pro"
MODE="${1:-}"

PACMAN_DEPS=(
  python python-gobject python-psutil python-pillow python-pystray
  gtk4 libadwaita wl-clipboard
  # optional but recommended - already present on the target
  mangohud gamemode
)

log()  { printf '\033[1;32m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m::\033[0m %s\n' "$*"; }

install_deps() {
  log "Installing dependencies via pacman"
  sudo pacman -S --needed --noconfirm "${PACMAN_DEPS[@]}"
}

install_python_pkg() {
  # No pip on a stock CachyOS Python - install the package tree straight into
  # the system site-packages dir and drop console-script shims in /usr/bin.
  local site
  site="$(python3 -c 'import site; print(site.getsitepackages()[0])')"
  log "Installing the goblinmode package into $site"
  sudo rm -rf "$site/goblinmode"
  sudo cp -r "$REPO_DIR/src/goblinmode" "$site/goblinmode"
  sudo python3 -m compileall -q "$site/goblinmode" || true

  sudo tee /usr/bin/goblin-mode-pro-daemon >/dev/null <<'EOF'
#!/usr/bin/python3
from goblinmode.daemon import main
raise SystemExit(main())
EOF
  sudo tee /usr/bin/goblin-mode-pro >/dev/null <<'EOF'
#!/usr/bin/python3
from goblinmode.gui.app import main
raise SystemExit(main())
EOF
  sudo chmod 755 /usr/bin/goblin-mode-pro-daemon /usr/bin/goblin-mode-pro
}

install_helper() {
  log "Installing privileged helper + polkit/D-Bus policy + system unit"
  sudo install -Dm755 "$REPO_DIR/helper/goblin_helper.py" "$HELPER_DIR/goblin_helper.py"
  sudo install -Dm644 "$REPO_DIR/data/polkit/com.goblinmode.pro.policy" \
    "$PREFIX/share/polkit-1/actions/com.goblinmode.pro.policy"
  sudo install -Dm644 "$REPO_DIR/data/dbus/com.goblinmode.ProHelper.conf" \
    "$PREFIX/share/dbus-1/system.d/com.goblinmode.ProHelper.conf"
  sudo install -Dm644 "$REPO_DIR/data/systemd/goblin-mode-pro-helper.service" \
    "$PREFIX/lib/systemd/system/goblin-mode-pro-helper.service"
  sudo systemctl daemon-reload
  sudo systemctl enable --now goblin-mode-pro-helper.service
}

install_user_bits() {
  log "Installing desktop entry, icon and user unit"
  sudo install -Dm644 "$REPO_DIR/data/com.goblinmode.Pro.desktop" \
    "$PREFIX/share/applications/com.goblinmode.Pro.desktop"
  sudo install -Dm644 "$REPO_DIR/data/icons/com.goblinmode.Pro.svg" \
    "$PREFIX/share/icons/hicolor/scalable/apps/com.goblinmode.Pro.svg"
  sudo install -Dm644 "$REPO_DIR/data/systemd/goblin-mode-pro.service" \
    "$PREFIX/lib/systemd/user/goblin-mode-pro.service"
  gtk-update-icon-cache -q "$PREFIX/share/icons/hicolor" 2>/dev/null || true

  systemctl --user daemon-reload
  systemctl --user enable --now goblin-mode-pro.service
  goblin-mode-pro-daemon --write-wrapper >/dev/null
  log "Set your Steam launch options to:  goblin-run %command%"
}

uninstall() {
  warn "Removing Goblin Mode Pro"
  systemctl --user disable --now goblin-mode-pro.service 2>/dev/null || true
  sudo systemctl disable --now goblin-mode-pro-helper.service 2>/dev/null || true
  sudo rm -f \
    "$PREFIX/share/polkit-1/actions/com.goblinmode.pro.policy" \
    "$PREFIX/share/dbus-1/system.d/com.goblinmode.ProHelper.conf" \
    "$PREFIX/lib/systemd/system/goblin-mode-pro-helper.service" \
    "$PREFIX/lib/systemd/user/goblin-mode-pro.service" \
    "$PREFIX/share/applications/com.goblinmode.Pro.desktop" \
    "$PREFIX/share/icons/hicolor/scalable/apps/com.goblinmode.Pro.svg"
  sudo rm -rf "$HELPER_DIR"
  local site
  site="$(python3 -c 'import site; print(site.getsitepackages()[0])')"
  sudo rm -rf "$site/goblinmode" /usr/bin/goblin-mode-pro-daemon /usr/bin/goblin-mode-pro
  sudo systemctl daemon-reload
  systemctl --user daemon-reload
  rm -f "$HOME/.local/bin/goblin-run"
}

case "$MODE" in
  --uninstall) uninstall ;;
  --user)
    install_deps
    install_python_pkg
    install_user_bits
    warn "Skipped the root helper - governor/renice/PL limits will be unavailable"
    ;;
  ""|--all)
    install_deps
    install_python_pkg
    install_helper
    install_user_bits
    log "Done. Open with 'goblin-mode-pro' or the app menu."
    ;;
  *) echo "usage: $0 [--user|--uninstall]"; exit 2 ;;
esac
