"""Microbenchmark for opt-in process health snapshots.

No services, privileged operations or energy settings are touched.
Measures the synchronous cost of one /proc read, not the cost of writing
alerts or the wakeup/scheduling overhead of a background sampling thread.
"""
import json
import platform
import statistics
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from auto_cpufreq.modules.process_health import read_snapshot


def benchmark(iterations=100):
    timings = []
    read_snapshot()  # Warmup
    for _ in range(iterations):
        started = time.perf_counter_ns()
        snapshot = read_snapshot()
        timings.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(timings)
    return {
        "operation": "read_snapshot own process",
        "iterations": iterations,
        "threads": snapshot.threads,
        "fds": snapshot.fds,
        "median_ms": round(statistics.median(timings), 4),
        "p95_ms": round(ordered[round(0.95 * (iterations-1))], 4),
        "max_ms": round(max(timings), 4),
        "estimated_cpu_duty_percent_at_5s": round(
            100 * statistics.median(timings) / 5000, 5
        ),
    }


if __name__ == "__main__":
    print(json.dumps({
        "benchmark": "issue-860 /proc process health overhead baseline",
        "python": platform.python_version(),
        "note": "Isolated synchronous /proc reads, not full diagnostic session",
        "result": benchmark(),
    }, sort_keys=True))
