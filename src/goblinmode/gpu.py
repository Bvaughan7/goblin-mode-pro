"""Deep NVIDIA GPU state probe - the stuff that explains a frame-rate cliff
that isn't thermal.

Every field here is queried straight from ``nvidia-smi``. The point is to snapshot
the GPU *at the moment of a stall* and again *after the game exits*, so an
extreme FPS dip can be pinned on a concrete cause:

* VRAM exhaustion -> the driver falls back to system memory over PCIe (huge stalls)
* PCIe link down-training -> Gen1 / narrow width = bandwidth starvation
* GPU stuck in a low power-state / collapsed core clock under load
* VRAM not released after the game exits -> a driver-side leak (needs a reboot)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)

_QUERY = (
    "utilization.gpu,utilization.memory,memory.used,memory.total,memory.free,"
    "clocks.current.graphics,clocks.max.graphics,clocks.current.memory,"
    "clocks.max.memory,pstate,pcie.link.gen.current,pcie.link.gen.max,"
    "pcie.link.width.current,pcie.link.width.max,temperature.gpu,"
    "power.draw,power.limit,clocks_event_reasons.active"
)
_FIELDS = [
    "util_gpu", "util_mem", "vram_used_mb", "vram_total_mb", "vram_free_mb",
    "clock_gfx_mhz", "clock_gfx_max_mhz", "clock_mem_mhz", "clock_mem_max_mhz",
    "pstate", "pcie_gen", "pcie_gen_max", "pcie_width", "pcie_width_max",
    "temp_c", "power_w", "power_limit_w", "event_reasons",
]


def available() -> bool:
    return shutil.which("nvidia-smi") is not None


def nvidia_module_state() -> dict:
    """Read-only: the current ``nvidia_drm.modeset`` parameter and, if
    present, the driver's reported GSP firmware version - both informational,
    neither is writable at runtime (modeset is a boot-time modprobe option;
    see ``helper.set_nvidia_modeset`` for changing the on-disk config)."""
    from pathlib import Path

    modeset_path = Path("/sys/module/nvidia_drm/parameters/modeset")
    try:
        modeset = modeset_path.read_text().strip() or None
    except OSError:
        modeset = None

    gsp_version = None
    gpus_dir = Path("/proc/driver/nvidia/gpus")
    try:
        for gpu_dir in gpus_dir.iterdir():
            info = (gpu_dir / "information").read_text()
            for line in info.splitlines():
                if line.lower().startswith("gsp firmware version"):
                    gsp_version = line.split(":", 1)[-1].strip()
                    break
            if gsp_version:
                break
    except OSError:
        pass

    return {
        "present": modeset_path.exists() or gpus_dir.exists(),
        "modeset": modeset,          # "Y" / "N" / None (unreadable or no nvidia_drm)
        "gsp_firmware_version": gsp_version,  # None means GSP is off or unreported
    }


def _num(v: str):
    v = v.strip()
    if v in ("", "[N/A]", "N/A", "[Not Supported]"):
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


_LIGHT_QUERY = "utilization.gpu,temperature.gpu,clocks_event_reasons.active"


def deep_state() -> dict:
    """A full GPU snapshot. Runs two ``nvidia-smi`` subprocesses (~0.1-1 s, but
    occasionally seconds under load) - **never call this from a GLib main loop**;
    use :class:`GpuMonitor` for the polled case."""
    if not available():
        return {}
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("nvidia-smi deep query failed: %s", exc)
        return {}
    if not out:
        return {}
    parts = [p.strip() for p in out[0].split(",")]
    state = {k: _num(parts[i]) for i, k in enumerate(_FIELDS) if i < len(parts)}
    state.update(_pcie_throughput())
    return state


def light_state() -> tuple[float | None, float | None, str]:
    """(gpu load %, temp °C, clock-event-reasons hex) - one cheap nvidia-smi."""
    if not available():
        return None, None, ""
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={_LIGHT_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None, None, ""
    if not out:
        return None, None, ""
    parts = [p.strip() for p in out[0].split(",")]
    try:
        return float(parts[0]), float(parts[1]), (parts[2] if len(parts) > 2 else "")
    except (ValueError, IndexError):
        return None, None, ""


class GpuMonitor:
    """Polls ``nvidia-smi`` on a background thread so the daemon's GLib loop
    never blocks on it. Everything degrades to empty when there is no NVIDIA GPU.

    * :meth:`light` - (load, temp, reasons), refreshed roughly every second
    * :meth:`deep`  - the full snapshot dict, refreshed roughly every 8 s
    """

    #: (light interval, deep interval) seconds - idle first, then while a game runs
    _IDLE = (6.0, 30.0)
    _ACTIVE = (1.0, 5.0)

    def __init__(self, deep_interval: float | None = None) -> None:
        self._active = False
        self._active_deep = deep_interval or self._ACTIVE[1]
        self._lock = threading.Lock()
        self._light: tuple[float | None, float | None, str] = (None, None, "")
        self._deep: dict = {}
        self._deep_at = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()

    def start(self) -> None:
        if not available() or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gmp-gpu", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def set_active(self, active: bool) -> None:
        """Poll fast while a game runs, slowly otherwise."""
        if active != self._active:
            self._active = active
            self._wake.set()  # apply the new cadence immediately

    def _run(self) -> None:
        while not self._stop.is_set():
            light_iv, deep_iv = self._ACTIVE if self._active else self._IDLE
            deep_iv = self._active_deep if self._active else deep_iv
            light = light_state()                      # subprocess - outside the lock
            with self._lock:
                self._light = light
            if time.monotonic() - self._deep_at >= deep_iv:
                deep = deep_state()                   # subprocess - outside the lock
                with self._lock:
                    self._deep = deep
                    self._deep_at = time.monotonic()
            self._wake.wait(light_iv)
            self._wake.clear()

    def light(self) -> tuple[float | None, float | None, str]:
        with self._lock:
            return self._light

    def deep(self, *, force: bool = False) -> dict:
        """Cached deep snapshot. ``force`` blocks for a fresh one - only call
        that from a worker thread (e.g. the FPS-dip handler)."""
        if force:
            fresh = deep_state()
            with self._lock:
                self._deep = fresh
                self._deep_at = time.monotonic()
            return fresh
        with self._lock:
            return dict(self._deep)


def _pcie_throughput() -> dict:
    """rx/tx PCIe throughput in MB/s from `nvidia-smi dmon` (one sample)."""
    if not available():
        return {}
    try:
        rows = subprocess.run(
            ["nvidia-smi", "dmon", "-s", "t", "-c", "1"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return {}
    for row in rows:
        row = row.strip()
        if not row or row.startswith("#"):
            continue
        cols = row.split()
        # "# Idx  rxpci  txpci" -> idx, rx, tx  (units MB/s)
        if len(cols) >= 3:
            return {"pcie_rx_mbps": _num(cols[1]), "pcie_tx_mbps": _num(cols[2])}
    return {}


def classify_dip(state: dict, cpu_load: float | None, disk_read_mbps: float | None) -> str | None:
    """When FPS collapses but nothing is working hard, the frames are being
    *withheld* (focus loss / loading screen), not *starved* (a bottleneck).
    Return a plain-language note if that's what this looks like, else None.
    """
    util = state.get("util_gpu")
    if util is None:
        return None
    gpu_idle = util < 15
    cpu_light = cpu_load is None or cpu_load < 45
    if gpu_idle and cpu_light:
        if disk_read_mbps and disk_read_mbps > 25:
            return (
                f"CPU and GPU were near-idle while the disk read {disk_read_mbps:.0f} MB/s "
                f"- this is a loading screen / zone transition streaming assets, not a bottleneck"
            )
        return (
            "CPU and GPU were both near-idle - the frames were withheld, not starved. "
            "Usually the game window losing focus (alt-tab -> WoW 'Max Background FPS' "
            "cap), a loading screen, or a menu. Not a hardware problem"
        )
    return None


def assess(state: dict, *, fps: float | None = None, under_load: bool = True) -> list[str]:
    """Return human-readable likely causes for a stall, most-damning first.

    ``under_load`` should reflect whether the GPU/CPU were actually busy - pass
    False and the PCIe / power-state / clock heuristics are skipped, because a
    down-trained link or a low P-state is *expected* when the GPU is idle.
    """
    if not state:
        return []
    out: list[str] = []

    # a real load check from the state itself, so a stale/optimistic under_load
    # can't produce false "bandwidth-starved" lines while the GPU sleeps
    util = state.get("util_gpu") or 0
    busy = under_load and util >= 25

    used, total = state.get("vram_used_mb"), state.get("vram_total_mb")
    free = state.get("vram_free_mb")
    if used and total:
        frac = used / total
        if frac >= 0.94 or (free is not None and free < 300):
            out.append(
                f"VRAM near exhaustion ({used}/{total} MB, {free} MB free) - the "
                f"driver is likely spilling to system RAM over PCIe"
            )
        elif frac >= 0.88:
            out.append(f"VRAM pressure high ({used}/{total} MB)")

    rx = state.get("pcie_rx_mbps")
    gen, gen_max = state.get("pcie_gen"), state.get("pcie_gen_max")
    w, w_max = state.get("pcie_width"), state.get("pcie_width_max")
    # only a bottleneck if the link is BOTH degraded AND actually being pushed
    if busy and gen and gen_max and gen < gen_max and (rx is None or rx > 500):
        out.append(f"PCIe link at Gen{gen} (card supports Gen{gen_max}) while carrying "
                   f"{rx} MB/s - bandwidth-starved")
    if busy and w and w_max and w < w_max and w <= 4 and (rx is None or rx > 500):
        out.append(f"PCIe link narrowed to x{w} (of x{w_max}) under load")

    ps = state.get("pstate")
    if busy and ps in ("P5", "P8", "P12", "P15") and util > 50:
        out.append(f"GPU stuck in low power-state {ps} while {util}% utilised")

    cg, cgm = state.get("clock_gfx_mhz"), state.get("clock_gfx_max_mhz")
    if busy and cg and cgm and cg < cgm * 0.55 and util > 50:
        out.append(f"GPU core clock collapsed to {cg}/{cgm} MHz under {util}% load")

    er = state.get("event_reasons")
    if isinstance(er, int) and er not in (0,):
        bad = {0x8: "HW slowdown", 0x40: "HW thermal", 0x80: "HW power-brake"}
        hit = [v for k, v in bad.items() if er & k]
        if hit:
            out.append("nvidia clock-event: " + ", ".join(hit))

    return out


def describe_dip(
    state: dict,
    *,
    fps: float,
    baseline: float,
    cpu_load: float | None,
    disk_read: float | None,
    cpu_core_max: float | None = None,
) -> tuple[str, bool]:
    """Turn a fresh GPU snapshot + the dip numbers into an incident line.

    Returns ``(detail, is_real)``. ``is_real`` is False for a dip that isn't a
    hardware *fault* - frames withheld (focus loss / loading), or a scene that's
    simply GPU- or CPU-bound at the current settings - so it doesn't arm the
    post-game post-mortem. Mutates ``state`` with the at-dip context the
    exporter and Diagnostics page read back.
    """
    gpu_busy = (state.get("util_gpu") or 0) >= 25 or (cpu_load or 0) >= 60
    benign = classify_dip(state, cpu_load, disk_read)
    causes = assess(state, fps=fps, under_load=gpu_busy)
    state["likely_causes"] = causes
    state["cpu_load_at_dip"] = round(cpu_load, 1) if cpu_load is not None else None
    state["cpu_core_max_at_dip"] = round(cpu_core_max, 1) if cpu_core_max is not None else None
    state["disk_read_mbps_at_dip"] = disk_read

    util = state.get("util_gpu")
    at = f"(baseline ~{baseline:.0f})"

    if benign and not causes:
        state["assessment"] = "benign - not a hardware bottleneck"
        return f"Frame rate dipped to {fps:.0f} FPS {at}. {benign}", False
    if causes:
        return f"Frame rate collapsed to {fps:.0f} FPS {at}. {causes[0]}", True

    dropped = baseline > 0 and fps <= baseline * 0.75
    if dropped and util is not None and util >= 92:
        state["assessment"] = "GPU-bound scene"
        return (
            f"Frame rate dropped to {fps:.0f} FPS {at}. GPU pegged at {util:.0f}% - "
            f"this spot is heavier than your settings can sustain, not a fault. "
            f"Lower a setting or cap the frame rate here.",
            False,
        )
    if dropped and util is not None and util < 80 and cpu_core_max is not None and cpu_core_max >= 95:
        state["assessment"] = "CPU-bound scene"
        return (
            f"Frame rate dropped to {fps:.0f} FPS {at}. A CPU core was pegged at "
            f"{cpu_core_max:.0f}% while the GPU had headroom ({util:.0f}%) - a "
            f"single-threaded hotspot (busy city, raid), not a hardware fault.",
            False,
        )
    return (
        f"Frame rate dropped to {fps:.0f} FPS {at}. No single cause stood out - "
        f"the GPU snapshot is attached. A short drop like this is usually a zone "
        f"load, shader compilation or a background task.",
        True,
    )


def post_mortem(idle_state: dict) -> tuple[str, str] | None:
    """After the game exits: did the GPU actually let go? Returns (kind, detail).

    Only VRAM is checked - an idle PCIe down-train is normal ASPM, not a fault.
    """
    used = idle_state.get("vram_used_mb")
    if used is not None and used > 900:
        return (
            "vram_not_freed",
            f"{used} MB of VRAM still allocated after the game exited - a "
            f"driver-side leak; a reboot clears it",
        )
    return None
