"""Wine / Proton log rule base - known Linux-gaming failure patterns.

Each rule maps a regex to a plain-language diagnosis and a fix. Used two ways:

* :mod:`goblinmode.logwatch` tails the live stderr and raises an incident on the
  rules flagged ``live`` (device-lost, OOM, crashes).
* :func:`analyze_text` scans a whole captured log for the one-click bug report
  and the Diagnostics "analyze log" action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: str
    label: str
    category: str          # gpu | memory | anticheat | runtime | deps | crash | config
    cause: str
    fix: str
    live: bool = False     # also watched on the live stderr for incidents
    severity: str = "warn"  # warn | error


RULES: tuple[Rule, ...] = (
    Rule("esync_fd", r"esync:.*up to \d+|eventfd: Too many open files|pipe\(\) failed.*Too many",
         "esync ran out of file descriptors", "config",
         "The open-file hard limit is too low for esync.",
         "Pre-flight -> raise the open-file limit (524288), or set PROTON_NO_ESYNC=1 for this game.",
         severity="error"),
    Rule("fsync_unsupported", r"fsync: warning|FUTEX_WAIT_MULTIPLE.*not|futex_waitv.*ENOSYS",
         "Kernel fsync not available", "config",
         "This kernel lacks futex_waitv; Proton falls back to esync (slower).",
         "Update to a kernel >= 5.16 (CachyOS ships current)."),
    Rule("vram_oom", r"VK_ERROR_OUT_OF_DEVICE_MEMORY|DXVK:.*Failed to allocate|CUDA.*out of memory|Failed to allocate .* device memory",
         "GPU ran out of video memory", "memory",
         "VRAM exhausted - the driver spills to system RAM over PCIe (huge stalls) or the game crashes.",
         "Lower texture quality / resolution; close other GPU apps; try DXVK's gplasync.",
         live=True, severity="error"),
    Rule("device_lost", r"VK_ERROR_DEVICE_LOST|vkQueueSubmit.*DEVICE_LOST|VKD3D.*(Device|Driver)\s+lost|D3D12.*device removed",
         "GPU device lost", "gpu",
         "The GPU stopped responding - driver bug, an unstable overclock/undervolt, overheating, or a VKD3D/DXVK issue.",
         "Reset any GPU OC; update the driver; check temps; try a different Proton (GE) build.",
         live=True, severity="error"),
    Rule("host_oom", r"std::bad_alloc|Out of memory|Oom|cannot allocate memory|MADV_.*failed",
         "Out of system memory", "memory",
         "System RAM exhausted.",
         "Enable zram/swap; close background apps; check for a memory leak in the game/Proton build.",
         live=True, severity="error"),
    Rule("anticheat", r"(EasyAntiCheat|EAC|BattlEye).*(not|unsupported|failed to (init|load))|AntiCheat.*Linux",
         "Anti-cheat not initialising", "anticheat",
         "The game's anti-cheat isn't starting - usually the Linux/Proton path isn't enabled for the title.",
         "Check areweanticheatyet.com; in Steam enable the Proton EAC/BattlEye runtime; some titles block Linux entirely."),
    Rule("wine_mono", r"wine: failed to load l?mscoree|Mono.*not installed|wine-mono",
         "wine-mono (.NET) missing", "deps",
         "The prefix has no .NET runtime.",
         "Let Proton install wine-mono (delete and recreate the prefix), or install it with protontricks."),
    Rule("vcrun", r"err:module:.*MSVC[PR]\d|api-ms-win-crt|vcruntime\d+\.dll.*not found",
         "Visual C++ runtime missing", "deps",
         "The game needs a Microsoft VC++ redistributable that isn't in the prefix.",
         "protontricks <appid> vcrun2022 (or the version the game bundles)."),
    Rule("dxvk_d3d", r"d3d11: Direct3D 11 is not supported|D3D_FEATURE_LEVEL.*fail|Failed to create D3D(9|11) device",
         "Direct3D device creation failed", "gpu",
         "DXVK couldn't create the D3D device - Vulkan driver missing in the prefix, or a feature-level mismatch.",
         "Verify a Vulkan ICD is installed; try Proton Experimental; check DXVK_HUD=1 loads."),
    Rule("vulkan_loader", r"Failed to load vulkan|vulkan-1\.dll.*not found|winevulkan.*not|No Vulkan.*ICD|ErrorIncompatibleDriver",
         "Vulkan not available to the game", "gpu",
         "The Vulkan loader or ICD isn't reachable.",
         "Install the vulkan driver for your GPU (pre-flight checks this); reinstall the Proton prefix."),
    Rule("shader_cache", r"Shader cache.*disabled|DISK_CACHE.*(failed|read-only)|__GL_SHADER_DISK_CACHE.*denied",
         "Shader disk cache not writable", "config",
         "Shaders can't be cached to disk -> constant recompilation stutter.",
         "Point __GL_SHADER_DISK_CACHE_PATH / DXVK_STATE_CACHE_PATH at a writable dir with space."),
    Rule("pressure_vessel", r"pressure-vessel.*(error|failed)|pv-bwrap.*failed|steam-runtime.*cannot",
         "Steam Linux Runtime container failed", "runtime",
         "The pressure-vessel sandbox couldn't start.",
         "Verify 'Steam Linux Runtime' is installed; try 'Runtime: Legacy' or force a Proton version."),
    Rule("page_fault", r"wine: Unhandled (page fault|exception)|err:seh:|Assertion .* failed|Segmentation fault",
         "The game process crashed", "crash",
         "An unhandled fault in the game or Proton.",
         "Note the module in the log; search ProtonDB for the title + that module; try another Proton build.",
         live=True, severity="error"),
    Rule("amdgpu_reset", r"amdgpu.*(ring .* timeout|GPU reset|GPU fault)|\[drm\].*reset",
         "amdgpu hang / reset", "gpu",
         "The AMD GPU hung and was reset.",
         "Try mesa-git; check for a known regression; disable any GPU OC; add amdgpu.gpu_recovery=1.",
         live=True, severity="error"),
    Rule("nvidia_xid", r"NVRM: Xid.*: (\d+)|nvidia.*Xid",
         "NVIDIA Xid error", "gpu",
         "The NVIDIA kernel driver logged a hardware/driver fault (Xid).",
         "Look up the Xid code; common ones mean OC instability, bad power, or overheating."),
    Rule("gl_mismatch", r"libGL error|MESA-INTEL:.*not supported|GLX.*Bad(Value|Match)|failed to load driver: (i965|iris|radeonsi)",
         "OpenGL driver / loader problem", "gpu",
         "The GL driver failed to load - often a 32-bit lib missing or a driver mismatch.",
         "Install the 32-bit GL/vulkan driver (lib32-*); ensure host and container drivers match."),
)

_COMPILED = [(re.compile(r.pattern, re.I), r) for r in RULES]
LIVE_PATTERNS = [(re.compile(r.pattern, re.I), r.label) for r in RULES if r.live]


@dataclass
class Finding:
    rule_id: str
    label: str
    category: str
    cause: str
    fix: str
    severity: str
    count: int
    sample: str


def analyze_text(text: str, max_per_rule: int = 1) -> list[Finding]:
    """Scan a whole log; return one Finding per matched rule, most severe first."""
    lines = text.splitlines()
    hits: dict[str, list[str]] = {}
    for line in lines:
        for rx, rule in _COMPILED:
            if rx.search(line):
                hits.setdefault(rule.id, []).append(line.strip()[:300])
    out: list[Finding] = []
    for rule in RULES:
        if rule.id in hits:
            samples = hits[rule.id]
            out.append(Finding(
                rule_id=rule.id, label=rule.label, category=rule.category,
                cause=rule.cause, fix=rule.fix, severity=rule.severity,
                count=len(samples), sample=samples[0],
            ))
    out.sort(key=lambda f: (f.severity != "error", f.category))
    return out
