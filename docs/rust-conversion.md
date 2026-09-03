# The Rust conversion

Goblin Mode Pro is being rewritten in Rust, component by component. This page
is the decision, the reasoning, the argument against it, and the current state
— written down because a migration that spans releases needs to survive the
memory of the person doing it.

!!! note "Scope"

    **The privileged helper is done and shipped.** It serves the frozen D-Bus
    contract, scores identically to the Python one on the same conformance
    suite, and has run a real game start to finish. It ships as an optional
    package and is enabled by pointing one symlink at it.

    **The conversion continues** through the domain logic, the daemon, the CLI
    and the GUI. At every stage the Python implementation stays installable and
    supported; the two meet only over frozen D-Bus interfaces, so neither side
    has to move before the other is ready.

    This page said the opposite until 2026-09-03 — "helper only… no plan to
    change that". That was true when it was written and the scope changed
    deliberately, so the old wording is recorded here rather than quietly
    replaced.

!!! warning "The justification changed too, and it got weaker"

    The helper port had a specific, defensible reason: **no interpreter running
    as root.** That argument is now spent. It does not extend to the daemon, the
    CLI or the GUI — none of them are privileged, and converting them buys
    nothing on that axis.

    The reason for continuing is a different one and is worth stating as itself
    rather than inheriting credit from the first: **one language, one toolchain,
    no Python runtime dependency, and one set of binaries to package.** Those
    are real benefits. They are maintenance and packaging benefits, not security
    ones, and this page will not describe them as security ones.

## What makes the swap possible at all

The daemon and the helper meet **only over D-Bus**. Neither imports the other.
That seam is what lets each side be written in a different language
independently, and it is the whole reason this is a contained project rather
than a rewrite of the application.

Two things make the seam trustworthy enough to swap an implementation across:

- **[`docs/dbus-interface-v1.xml`](https://github.com/Bvaughan7/goblin-mode-pro/blob/main/docs/dbus-interface-v1.xml)** — the frozen contract. 19 methods on
  `com.goblinmode.ProHelper.Manager`. `tests/test_dbus_interface_freeze.py`
  compares what the running helper *serves* against that file on every push
  (CI job `interface-freeze`). The comparison runs on a canonical rendering
  rather than raw introspection bytes, because GDBus and zbus disagree about
  indentation, attribute order and which standard interfaces they append — a
  raw byte diff would fail on the first Rust commit for purely cosmetic
  reasons, and a freeze check that fails cosmetically gets regenerated out of
  irritation and stops protecting anything.
- **`tests/conformance/helper.py`** — a suite that drives the helper from
  outside, over the system bus, and never imports its source. It does not know
  or care which language answered. It grades either implementation on the same
  scale, which is what turns "the Rust one seems fine" into a number.

Against the Python helper on real hardware, that suite currently scores
**39 PASS / 0 FAIL / 1 SKIP** as root, including runtime confirmation of all 15
polkit routings, plus a second unprivileged run that closes the remaining skip.

The two runs grade **disjoint** sets and neither alone is complete, which is
easy to miss because each one looks like the whole suite. Root is needed to read
the root-only state directory and to eavesdrop the bus for polkit routing.
Unprivileged is the *only* way to see the ownership gate, because `renice()`
skips it for uid 0 - so the run with the most privilege is the one that cannot
check who you are. Across both runs every check passes and nothing fails; the
suite now prints the complementary command whenever it skips anything.
That is the bar the Rust helper has to clear — a measured baseline, not an
aspiration.

## The argument that earns the rest of this page

Freezing the interface and writing an external conformance suite found a real
bug on the suite's **first run against real hardware**, before a line of Rust
did anything:

`set_power_limits()` called `_snapshot()` as its first statement and validated
its arguments inside the write loop. A request below the 6 W RAPL floor was
correctly refused — and still left a root-owned `state.json` in `/run`. Because
`_snapshot()` early-returns once that file exists, the *next* legitimate apply
never recorded its own baseline, so a later `RevertAll` would restore whatever
had been true at the moment of the rejected call. Every sibling method
validated first; this one was the odd one out.

No unit test caught it, and the reason is worth keeping: the test that covered
the floor **patched `_snapshot` out**. It mocked away the side effect that was
the bug. An in-process test written by the same person who wrote the code
tends to share its blind spots; a suite that can only see the D-Bus surface and
the filesystem does not. The conformance suite now asserts the general rule on
every rejection — a refused call must leave *no* state behind, not merely leave
its target value alone.

Fixed in [`34e600f`](https://github.com/Bvaughan7/goblin-mode-pro/commit/34e600f).

## Why Rust, for this component

- **The privileged component is the one worth hardening.** The helper is the
  only piece running as root, holding `CAP_SYS_NICE` and `CAP_SYS_RESOURCE`,
  writing sysfs, sysctls, fan PWM and `/etc/modprobe.d`, reachable by anything
  on the system bus. A memory-safety or logic bug there is a security bug. The
  same bug in the GUI is a bad afternoon.
- **No interpreter inside the root process.** Dropping CPython and PyGObject
  out of a root-privileged, bus-reachable service removes a large amount of
  code that has to be trusted. One concrete example: the unit sets
  `MemoryDenyWriteExecute=yes` today, but only behind a caveat — PyGObject
  dispatches the D-Bus handler through a libffi closure, so the directive is
  safe only on libffi ≥ 3.4.2 and would break the helper on older systems with
  no error message worth reading. A Rust binary has no libffi trampolines and
  the caveat disappears with the comment explaining it.
- **Fail-closed becomes a type rather than a comment.** `caller_uid` returns
  `Option<u32>`, so every caller is forced to say what an unresolvable uid
  means. The Python helper shipped exactly that bug once: an unresolvable uid
  was allowed to stand in for root, which handed `Renice`'s ownership check to
  anyone who could make the lookup fail. The Rust port carries a comment
  saying so, because the obvious translation of an `Option` into a default is
  precisely what reintroduces it.
- **Rollback is a symlink, not a migration.** Block H1 puts a single unit at
  `/usr/libexec/goblin-mode-pro/helper` with a symlink choosing the
  implementation, so going back to the Python helper is a symlink swap and a
  restart — something a user can do over SSH at midnight when their machine is
  misbehaving. That property is what makes shipping the Rust helper to other
  people defensible at all.
- **It forces packaging honesty.** Both the `.deb` and the `.rpm` are still
  `noarch` / `all`. The moment a compiled binary ships they become `x86_64` /
  `amd64`, which is what they should have said all along.

**Speed is explicitly not a reason.** The migration plan says to leave
performance alone, and the helper is idle almost all the time anyway — it is
asked to do a handful of things per gaming session. Any page that sold this
rewrite as a speed win would be selling something that was never measured.

## The argument against, recorded rather than buried

The migration plan's own closing note deserves to sit in this document:

> `selftest`, the AUR publish and the verified-hardware table will do more for
> this project in the next month than a language migration will, because the
> constraint right now is that seven people have starred it and approximately
> zero have run it. Rust makes the privileged component better. It doesn't
> make anyone install the tool.

That is correct, and it is not a reason to stop — it is a reason to be honest
about the ordering. The decision is to do the conversion anyway: the helper is
genuinely the right piece to learn Rust on, and "I want to write Rust and this
is the project I have" is a legitimate reason as long as it is stated out loud
rather than dressed up as engineering necessity. The condition attached to it
is that it **must not stall the distribution work**. A design document that
only argues one side ages badly.

**Extending that argument to the rest of the conversion, honestly.** Everything
above applies more strongly now, not less. The remaining work is roughly 10,700
lines of Python; the one block already done expanded 1,202 Python lines into
3,886 of Rust, so the rest is plausibly 35,000 lines. That is a great deal of
effort for benefits that are real but modest, on a project whose actual
constraint is still that very few people have run it.

Three things make it defensible anyway, and they should be weighed rather than
assumed:

- **The seams are proven, not hoped for.** Both D-Bus interfaces are frozen and
  graded from outside by suites that never import what they test. Each one found
  a real bug on its first run — `SetPowerLimits` writing state before validating,
  and `IgnoreGame` having no inverse anywhere in the project.
- **The method is proven.** Port a group, diff it against the Python answers on
  real hardware, keep both implementations passing at every commit. It has now
  survived one full component including the riskiest operation in the codebase.
- **Nothing is forced.** The Python implementation stays installable at every
  stage. If the conversion stalls, what exists still works and still ships.

What would make stopping the right call: if the port starts displacing
distribution work again, or if feedback from actual users turns out to be about
hardware compatibility rather than anything a language change touches. Neither
is true today. Both are worth re-checking at each block rather than at the end.

## Corrections to the migration plans

The conversion is being worked from three external plan documents. All three
get the same details wrong about this repository, and anyone building from
them unamended will build the wrong thing.

1. **The bus name is `com.goblinmode.ProHelper`**, the object path is
   `/com/goblinmode/ProHelper`, and the interface is
   `com.goblinmode.ProHelper.Manager`. The plans say
   `com.goblinmode.pro.Helper1`. A helper built to that name claims a bus
   nobody talks to and fails silently — the daemon simply never gets an
   answer.
2. **`SetPowerLimits` above the firmware maximum is not a rejection.** It
   clamps to the zone cap and succeeds. The refusal is on the *low* side,
   below `_RAPL_FLOOR_UW` = 6 W. A conformance expectation written the other
   way round tests nothing.
3. **The conformance suite needs an active local session, not root.** The
   policy grants `manage-performance` with `allow_active=yes`, so under
   `sudo`-from-SSH every mutating method is denied by policy and the suite
   measures only that denial. (`sudo` from a desktop terminal keeps the
   session and works. `--polkit-routing` is the one mode that genuinely needs
   root, to eavesdrop the bus.)

### And a warning that cost a working afternoon

Running the suite against a live desktop raises real polkit dialogs on the
user's screen. The helper passes `AllowUserInteraction=1`, and **the client
cannot suppress it**. Four suite runs left three prompts unanswered, which
tripped `pam_faillock` (`deny=3`) and locked the user out of `sudo` for ten
minutes with a correct password — `faillock --user <name>` showed the source
as `polkit-1`. Everything gated behind an `auth_admin_keep` action is
therefore a SKIP unless `--prompts` is passed explicitly.

## Two design facts that are not obvious

**One unit, not two.** The deployment addendum's block H1 supersedes the
migration plan's R4. There will be a single unit pointing at
`/usr/libexec/goblin-mode-pro/helper`, with a symlink selecting the
implementation — *not* two units with `Conflicts=`. Rollback is then a
symlink swap and a restart, which is something a user can do over SSH at
midnight, rather than a systemd puzzle.

**`/run/goblin-mode-pro/state.json` is a two-way compatibility surface.**
Either implementation may find a file written by the other, and a user who
rolls back mid-session must not lose the baseline their machine gets restored
to. Python's `json` is permissive and `serde` is strict by default, and that
asymmetry is where a silent break would live. Three rules follow, all
load-bearing:

- `#[serde(default)]` on every field, so a file from an **older** version
  loads;
- never `deny_unknown_fields`, so a file from a **newer** version loads — that
  is the rollback path;
- unknown keys are preserved on rewrite, so the Rust helper cannot silently
  strip a field a newer Python helper added.

Numbers get the same treatment: Python writes `4` and `4.0` interchangeably
depending on how a value was computed, so anything that might have been
through a float is parsed permissively rather than demanding an integer token.

## Where it stands

Block by block, tracked in [issue #1](https://github.com/Bvaughan7/goblin-mode-pro/issues/1):

| Block | What it is | State |
|---|---|---|
| **R1** | Freeze the D-Bus contract; build an implementation-agnostic conformance suite | **Done.** 39 PASS / 0 FAIL / 1 SKIP on hardware; found the `SetPowerLimits` bug |
| **R2** | Cargo workspace, the polkit authorization path, the state snapshot, and all 19 methods as refusing stubs | **Done.** The binary serves the frozen contract; `--introspect` is graded byte for byte by the same canonicalizer the Python helper goes through |
| **R3** | Port the hardware operations, group by group | **Done, and verified on hardware.** The Rust helper served the conformance suite live and scored identically to the Python one - 39/0 root, 19/0 unprivileged. See [verified hardware](verified-hardware.md) |
| **H1** | One unit, symlinked implementation, rollback as a drop-in | **Done.** The unit runs `/usr/libexec/goblin-mode-pro/helper`, a symlink, verified on hardware. `install.sh --helper=rust` builds, contract-checks and installs the Rust binary; Python is installed either way so rolling back needs no toolchain |
| **P2** | Freeze the daemon's session-bus interface, and grade it from outside | **Done.** `docs/dbus-daemon-interface-v1.xml` (29 methods, 5 signals, 3 properties) + `tests/conformance/daemon.py`. Baseline on real hardware: **23 PASS / 0 FAIL / 9 SKIP** — it was 1 FAIL on the first run, and that bug is fixed |
| **P0** | State the widened scope publicly, and say plainly that the original justification does not extend to it | **Done.** This page, the README and the ROADMAP |
| **P3** | `gmp-core` — the domain logic, tests translated first, module by module | **Done. All 12 modules**, each with a parity harness that asks both implementations the same questions and diffs the answers. 602 Python tests, 263 Rust |
| **P4** | `gmp-daemon` and `gmp-cli` | **In progress.** Nine judgement slices are ported, and `crates/gmp-daemon` serves the frozen interface - graded byte for byte by the same canonicalizer the Python daemon goes through - with the three disk-backed read methods answering for real and the rest refusing. What is left is the poll loop, the apply/revert path and the state they own: a rewrite against that contract rather than an extract-and-diff |
| **P5** | `gmp-gui` — gtk4-rs, `ksni` for the tray, and the i18n msgids preserved character for character | Not started. ~3,300 lines |
| **P6** | Cutover: delete the Python, repackage, re-verify every capability under Rust | Not started |
| **H5** | `.deb` / `.rpm` become architecture-specific | **Done, differently.** Making the whole package architecture-specific would drop every non-x86 user of a package that is otherwise pure Python. The compiled helper is a separate optional x86_64 package instead; the main package stays `all`/`noarch` |

The Rust sources live in `crates/gmp-helper/`. `cargo test` covers the polkit
routing table method by method (a privilege boundary is pinned explicitly, not
re-derived from the same sets the code is built from) and the snapshot
format's compatibility rules, including a fixture captured from the Python
helper itself.

What the port has found so far, none of which was in the plan:

- **The D-Bus error NAMES are part of the contract**, though the frozen XML
  covers only methods and signatures. The conformance suite matches on
  `com.goblinmode.ProHelper.Manager.NotAuthorized` to tell a refusal from a
  breakage, so returning the standard `org.freedesktop.DBus.Error.AccessDenied`
  - which is what reaching for `zbus::fdo::Error` gives you - would have been
  graded as a failure by this project's own suite while looking correct.
- **Refusal MESSAGES are contract too.** The suite matches fragments:
  "unsupported governor", "unsupported epp", "not in allowlist", "out of
  range", "non-numeric".
- **70% fan duty is 178, not 179.** Python's `round()` rounds half to even and
  70% of 255 is exactly 178.5. It is the only value in the permitted 40-100
  range where the two disagree.
- **zbus derives PascalCase from Rust method names**, which would have served
  `SetEpp`, `SetTdp`, `ResetTdp` and `HasTdpControl` - four methods the daemon
  calls and would never have reached.
- **rustix cannot express `pidfd_send_signal(fd, 0)`**, the null signal the
  Python uses for liveness, because its `Signal` is non-zero by construction
  and this crate forbids `unsafe`. The pidfd's `/proc/self/fdinfo` entry
  answers the same question instead.

Freezing the *daemon's* interface did the same thing on its first run:
**ignoring a game could not be undone.** `IgnoreGame` appended to
`ignored_games` and nothing anywhere removed an entry — not the daemon, not the
interface, not the GUI, whose Ignore button was therefore a one-way door.
`KeepGame` is not its inverse; it clears `auto_created` on a profile.

Fixed by adding `UnignoreGame` plus a Restore control in the GUI. Two things
that made it a better bug than it looked: the suite found it by *committing* it
— the first version assumed `KeepGame` was the inverse and left a sentinel
permanently in the settings of the machine it was grading — and the suite's
round-trip now probes the inverse *before* using the forward operation, so a
daemon too old to have it is skipped rather than damaged.

### What porting the domain logic found

`gmp-core` is now complete: twelve modules, each with an `examples/` binary
that reads JSON on stdin and prints JSON, and a parity test that puts the same
questions to both implementations and diffs the answers. The point of building
it that way is that a disagreement shows up as a test failure rather than as an
opinion. Six of the twelve turned up something.

- **A hand-broken config file crashed the daemon at startup.** `_from_dict`
  caught `ValueError` and `TypeError` but not `AttributeError`, and three
  shapes reach `.strip()` or `.setdefault()` on the wrong type: `exe` as a
  number, `mangohud` as a list, `gamescope` as a string. The exception escaped
  the loader entirely and took down whatever was reading the file — the daemon,
  the GUI and the launch wrapper alike. The loop it escaped was written to
  "drop a corrupt / hand-broken entry rather than fail to start".
- **`^...$` accepts a trailing newline, in three validators.** Python's `$`
  also matches just before a trailing newline, so `SCX_NAME_RE`,
  `_ENV_NAME_RE` and `_ENV_VALUE_RE` all accepted a value their own character
  classes were written to reject. Not an injection — interior newlines are
  still refused, so no attacker-chosen second assignment can be produced — but
  a name ending in a newline was emitted as `FOO\n=1` and read back by the
  wrapper as `FOO` with an empty value, silently dropping the setting.
- **Lutris detection never matched a running game.** `lutris-wrapper` renames
  itself to `lutris-wrapper: <title>`, with a colon, and the pattern required
  whitespace. Every Lutris game lost its launcher score and its display name.
- **`round(x, 1)` diverged by a factor of ten** in the session statistics, and
  the preflight advice shipped with ten spaces in the middle of a sentence —
  both artefacts of generating Rust text from Python source without treating
  the string as raw.
- **Both of Python's dict-ordering rules turned out to be load-bearing** in the
  display code: the first matching mode id wins, and re-assigning an existing
  key keeps its position while replacing its value. A `HashMap` reproduces
  neither.

Two properties of the config format are reproduced deliberately even though
both look like defects. **Unknown keys are dropped, not preserved** — a key
written by a newer build does not survive an older build saving the file — and
**nothing is type-coerced**, so a boolean field holding the string `"yes"`
stays that string. A port that improved on either would round-trip files the
Python would not, and the two would stop being interchangeable. Changing them
is a schema decision to take on both sides at once, not a side effect of a
rewrite.

Two bugs the Python helper once had are pinned by tests that fail if the
translation reintroduces them: snapshotting before validating (a refused call
must leave no state behind), and letting an unresolvable caller uid stand in
for root.

Nothing about the Python helper changes while this is built. It remains the
shipped implementation until the Rust one clears the same suite on the same
hardware.
