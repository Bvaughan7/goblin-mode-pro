from goblinmode import gpu


def test_assess_flags_vram_exhaustion():
    causes = gpu.assess(
        {"vram_used_mb": 5950, "vram_total_mb": 6144, "vram_free_mb": 190},
        under_load=True,
    )
    assert any("VRAM near exhaustion" in c for c in causes)


def test_assess_flags_pcie_downtrain_only_when_busy_and_pushing_traffic():
    busy = {"pcie_gen": 1, "pcie_gen_max": 3, "pcie_width": 8, "pcie_width_max": 16,
            "util_gpu": 90, "pcie_rx_mbps": 2000}
    assert any("Gen1" in c for c in gpu.assess(busy, under_load=True))
    # same link state but the GPU is idle and the bus is quiet -> not a cause
    idle = dict(busy, util_gpu=1, pcie_rx_mbps=15)
    assert gpu.assess(idle, under_load=True) == []


def test_assess_flags_stuck_pstate_and_clock():
    causes = gpu.assess(
        {"pstate": "P8", "util_gpu": 95, "clock_gfx_mhz": 300, "clock_gfx_max_mhz": 1900},
        under_load=True,
    )
    assert any("low power-state P8" in c for c in causes)
    assert any("core clock collapsed" in c for c in causes)


def test_classify_dip_idle_is_focus_or_loading():
    idle = {"util_gpu": 1, "pstate": "P5"}
    note = gpu.classify_dip(idle, cpu_load=25.0, disk_read_mbps=2.0)
    assert note and "withheld" in note

    loading = gpu.classify_dip(idle, cpu_load=25.0, disk_read_mbps=180.0)
    assert loading and "loading screen" in loading

    # genuinely busy -> no benign note
    assert gpu.classify_dip({"util_gpu": 95}, cpu_load=80.0, disk_read_mbps=0.0) is None


def test_assess_quiet_on_healthy_state():
    assert gpu.assess(
        {
            "vram_used_mb": 3200, "vram_total_mb": 6144, "vram_free_mb": 2900,
            "pcie_gen": 3, "pcie_gen_max": 3, "pcie_width": 8, "pcie_width_max": 16,
            "pstate": "P0", "util_gpu": 98, "clock_gfx_mhz": 1800, "clock_gfx_max_mhz": 1900,
            "event_reasons": 0, "pcie_rx_mbps": 1200,
        },
        under_load=True,
    ) == []


def test_assess_ignores_gen2_link_at_idle_the_users_false_positive():
    # the real incident: GPU idle (1%), link at Gen2, 15 MB/s traffic
    state = {
        "util_gpu": 1, "vram_used_mb": 1604, "vram_total_mb": 6144, "vram_free_mb": 4143,
        "pcie_gen": 2, "pcie_gen_max": 3, "pcie_width": 8, "pcie_width_max": 16,
        "pstate": "P5", "clock_gfx_mhz": 675, "clock_gfx_max_mhz": 2100,
        "pcie_rx_mbps": 15, "event_reasons": 1,
    }
    assert gpu.assess(state, under_load=True) == []


def test_post_mortem_catches_leak_and_ignores_clean():
    assert gpu.post_mortem({"vram_used_mb": 2400})[0] == "vram_not_freed"
    assert gpu.post_mortem({"vram_used_mb": 15}) is None


def test_pstate_not_flagged_when_idle():
    # under_load=False -> a low pstate / narrow link is expected, not an incident
    assert gpu.assess(
        {"pstate": "P8", "util_gpu": 0, "pcie_gen": 1, "pcie_gen_max": 3},
        under_load=False,
    ) == []
