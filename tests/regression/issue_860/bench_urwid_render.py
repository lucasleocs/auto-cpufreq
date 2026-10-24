"""Synthetic Urwid format benchmark; no terminal opened, no power writes.

Run in experimental CLI test environment:
  python tests/regression/issue_860/bench_urwid_render.py

Measures SystemMonitor.format_system_info rebuilding its list walkers;
does not measure terminal rasterization, PTY I/O or system telemetry.
"""
import json
import platform
import statistics
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def load_monitor():
    config = types.ModuleType("auto_cpufreq.config.config")
    config.config = types.SimpleNamespace(
        path="/dev/null", has_config=lambda: False,
    )
    info = types.ModuleType("auto_cpufreq.modules.system_info")
    info.SystemReport = object
    info.battery_power_label = lambda battery: "Battery power"
    info.format_platform_profile_summary = lambda _: []
    info.system_info = types.SimpleNamespace()
    with patch.dict(sys.modules, {
        config.__name__: config,
        info.__name__: info,
    }):
        from auto_cpufreq.modules.system_monitor import SystemMonitor, ViewType
        return SystemMonitor, ViewType


COUNTS = (4, 8, 16, 32)
ITERATIONS = 60
WARMUP = 5


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def report(n):
    return types.SimpleNamespace(
        distro_name="TestLinux", distro_ver="1.0", arch="x86_64",
        processor_model="Synthetic CPU", total_core=n,
        kernel_version="test", current_gov="powersave",
        current_epp="balance_power", current_epb="normal",
        current_hwp_dynamic_boost=False, cpu_driver="intel_pstate",
        cpu_fan_speed=None, cpu_usage=6.0,
        cpu_max_freq=3500.0, cpu_min_freq=1000.0, load=0.5,
        avg_load=(0.5, 0.4, 0.3),
        cores_info=[
            types.SimpleNamespace(id=i, usage=i * 2.0 % 100,
                                  temperature=40 + i / 10,
                                  frequency=2000 + 25 * i)
            for i in range(n)
        ],
        battery_info=None, is_turbo_on=(True, None), cpu_avg_temp=44.0,
        platform_profile=types.SimpleNamespace(),
    )


def bench(count, monitor_class, view_type):
    monitor = monitor_class(type=view_type.STATS)
    sample = report(count)
    for _ in range(WARMUP):
        monitor.format_system_info(sample)

    timings = []
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    for _ in range(ITERATIONS):
        start = time.perf_counter_ns()
        monitor.format_system_info(sample)
        timings.append((time.perf_counter_ns() - start) / 1_000_000)

    elapsed_wall = time.perf_counter() - wall_started
    elapsed_cpu = time.process_time() - cpu_started
    left = len(monitor.left_content)
    right = len(monitor.right_content)
    assert left >= count
    return {
        "frontend": "urwid",
        "operation": "SystemMonitor.format_system_info",
        "logical_cores": count,
        "iterations": ITERATIONS,
        "widgets_after_refresh": left + right,
        "median_ms": round(statistics.median(timings), 4),
        "p95_ms": round(percentile(timings, 0.95), 4),
        "max_ms": round(max(timings), 4),
        "cpu_ms_per_refresh": round(1000 * elapsed_cpu / ITERATIONS, 4),
        "wall_ms_per_refresh": round(1000 * elapsed_wall / ITERATIONS, 4),
    }


if __name__ == "__main__":
    SystemMonitor, ViewType = load_monitor()
    print(json.dumps({
        "benchmark": "issue-860 Urwid listwalker rebuild baseline",
        "python": platform.python_version(),
        "note": "Synthetic CPU cores; excludes terminal screen/PTY and sensors",
        "results": [bench(n, SystemMonitor, ViewType) for n in COUNTS],
    }, sort_keys=True))
