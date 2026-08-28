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


def deep_state() -> dict:
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


def post_mortem(idle_state: dict) -> tuple[str, str] | None:
    """After the game exits: did the GPU actually let go? Returns (kind, detail)."""
    used = idle_state.get("vram_used_mb")
    if used is not None and used > 900:
        return (
            "vram_not_freed",
            f"{used} MB of VRAM still allocated after the game exited - a "
            f"driver-side leak; a reboot clears it",
        )
    gen, gen_max = idle_state.get("pcie_gen"), idle_state.get("pcie_gen_max")
    if gen and gen_max and gen < gen_max and (idle_state.get("util_gpu") or 0) < 5:
        # idle down-train is normal ASPM - only flag if it looks stuck oddly low
        return None
    return None
