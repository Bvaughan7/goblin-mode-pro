# The privileged helper's Rust implementation, packaged on its own.
#
# A SEPARATE spec rather than a subpackage of goblin-mode-pro.spec, because rpm
# refuses an architecture-specific subpackage of a noarch package:
#
#   error: Only noarch subpackages are supported: BuildArch: x86_64
#
# noarch-inside-arch is allowed; arch-inside-noarch is not. The main package is
# pure Python and should stay installable everywhere - including the aarch64
# handhelds and ARM boards in this audience - so the compiled half moves out
# into its own build instead of dragging the whole package to x86_64.
Name:           goblin-mode-pro-helper-rust
Version:        1.5.0
Release:        1%{?dist}
Summary:        Privileged helper for Goblin Mode Pro, Rust implementation

License:        MIT
URL:            https://github.com/Bvaughan7/goblin-mode-pro
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz#/goblin-mode-pro-%{version}.tar.gz

ExclusiveArch:  x86_64
BuildRequires:  cargo
BuildRequires:  rust >= 1.82
Requires:       goblin-mode-pro = %{version}-%{release}

%description
The privileged helper rewritten in Rust, serving the same frozen D-Bus
interface as the Python one that ships in goblin-mode-pro.

Installing this does NOT switch to it. The unit runs
%{_prefix}/libexec/goblin-mode-pro/helper, a symlink that the main package
points at the Python helper. To switch:

  sudo ln -sfn %{_prefix}/libexec/goblin-mode-pro/helper-rust \
               %{_prefix}/libexec/goblin-mode-pro/helper
  sudo systemctl restart goblin-mode-pro-helper

Roll back by pointing the symlink at
%{_prefix}/lib/goblin-mode-pro/goblin_helper.py and restarting. The Python
helper is in the main package and always present, so a rollback never needs a
toolchain.

%prep
%autosetup -n goblin-mode-pro-%{version}

%build
cargo build --release --locked -p gmp-helper

%install
install -Dm0755 target/release/gmp-helper \
  %{buildroot}%{_prefix}/libexec/goblin-mode-pro/helper-rust

%files
%license LICENSE
%{_prefix}/libexec/goblin-mode-pro/helper-rust

%changelog
* Thu Sep 03 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.5.0-1
- See CHANGELOG.md for the full list of changes.
* Wed Sep 02 2026 Bryan Vaughan <bryanvaughan07@gmail.com> - 1.4.0-1
- First release. See CHANGELOG.md.
