Name:           goblin-mode-pro
Version:        1.4.0
Release:        1%{?dist}
Summary:        One-switch performance helper for Linux gaming

License:        MIT
URL:            https://github.com/Bvaughan7/goblin-mode-pro
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildRequires:  python3
BuildRequires:  systemd-rpm-macros

Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       python3-psutil
Requires:       gtk4
# >=1.5: AlertDialog / AboutDialog / Breakpoint - see
# MIN_ADW_VERSION in src/goblinmode/gui/app.py
Requires:       libadwaita >= 1.5
Requires:       polkit

Recommends:     mangohud
Recommends:     gamemode
Recommends:     gamescope
Recommends:     wl-clipboard
Recommends:     python3-pillow
Recommends:     python3-pystray
Recommends:     python3-cairo
Suggests:       ryzenadj

# Pure Python, so it installs anywhere. The compiled helper is a SEPARATE
# spec (goblin-mode-pro-helper-rust.spec), not a subpackage: rpm refuses an
# arch-specific subpackage of a noarch package - "Only noarch subpackages
# are supported" - so the split has to be two builds.
BuildArch:      noarch
Suggests:       intel-undervolt
Suggests:       gpu-screen-recorder

%global libdir %{_prefix}/lib/%{name}

%description
Detects a game launching, tunes the CPU governor, process priority, compositor
and power limits, and reverts them cleanly on exit - then watches thermals,
frame rate and the Proton log and turns a problem into a plain-language report.

An unprivileged systemd --user daemon does the work; a small polkit-gated root
helper performs the handful of privileged sysfs writes.

%prep
%autosetup

%build
python3 -m compileall -q src/goblinmode || true

%install
install -d %{buildroot}%{libdir}
cp -rT src/goblinmode %{buildroot}%{libdir}/goblinmode
install -Dm0755 helper/goblin_helper.py %{buildroot}%{libdir}/goblin_helper.py
# the unit runs this symlink, not the file directly - see install.sh
install -d %{buildroot}/usr/libexec/%{name}
ln -sfn %{libdir}/goblin_helper.py %{buildroot}/usr/libexec/%{name}/helper

%files
%license LICENSE
%doc README.md SECURITY.md
%{libdir}/
%{_bindir}/goblin-mode-pro
%{_bindir}/goblin-mode-pro-daemon
%{_bindir}/goblin-mode-pro-cli
%{_datadir}/polkit-1/actions/com.goblinmode.pro.policy
%{_datadir}/dbus-1/system.d/com.goblinmode.ProHelper.conf
%{_unitdir}/goblin-mode-pro-helper.service
%{_userunitdir}/goblin-mode-pro.service
%{_datadir}/applications/com.goblinmode.Pro.desktop
%{_datadir}/applications/com.goblinmode.Pro.GamescopeSession.desktop
%{_datadir}/icons/hicolor/scalable/apps/com.goblinmode.Pro.svg
%{_datadir}/icons/hicolor/*/apps/com.goblinmode.Pro.png
%{_datadir}/icons/hicolor/*/apps/goblin-mode-pro.png
%{_datadir}/%{name}/

%changelog
* Wed Sep 02 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.4.0-1
- See CHANGELOG.md for the full list of changes.
* Wed Sep 02 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.3.2-1
- See CHANGELOG.md for the full list of changes.
* Mon Aug 31 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.3.1-1
- See CHANGELOG.md for the full list of changes.
* Mon Aug 31 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.3.0-1
- See CHANGELOG.md for the full list of changes.
* Mon Aug 31 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.2.3-1
- See CHANGELOG.md for the full list of changes.
* Mon Aug 31 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.2.2-1
- See CHANGELOG.md for the full list of changes.
* Sun Aug 30 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.2.1-1
- See CHANGELOG.md for the full list of changes.
* Sat Aug 29 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.2.0-1
- See CHANGELOG.md for the full list of changes.
* Sat Aug 29 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.1.0-1
- See CHANGELOG.md for the full list of changes.
