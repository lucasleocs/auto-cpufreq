"""Reproducible widget-rebuild baseline under Xvfb; does not optimize UI.

Run under Linux GTK 3:
  xvfb-run -a /usr/bin/python3 tests/regression/issue_860/bench_gtk_render.py

This measures only SystemReportView._refresh_core_rows(), not graphics
compositing, full-window layout or hardware collection.
"""
import json
import platform
import statistics
import time
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from gtk_refresh_checks import APP


COUNTS = (4, 8, 16, 32)
ITERATIONS = 60
WARMUP = 5


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def bench(count):
    view = APP.SystemReportView()
    cores = [
        SimpleNamespace(
            id=i, usage=(i * 7.25) % 100, temperature=40.0 + i / 10,
            frequency=1600.0 + i * 10,
        )
        for i in range(count)
    ]

    for _ in range(WARMUP):
        view._refresh_core_rows(cores)

    samples = []
    started_cpu = time.process_time()
    started_wall = time.perf_counter()
    for _ in range(ITERATIONS):
        t0 = time.perf_counter_ns()
        view._refresh_core_rows(cores)
        samples.append((time.perf_counter_ns() - t0) / 1_000_000)

    elapsed_wall = time.perf_counter() - started_wall
    elapsed_cpu = time.process_time() - started_cpu
    widget_count = len(view.cores_grid.get_children())
    assert widget_count == (count + 1) * 4
    result = {
        "frontend": "gtk3",
        "operation": "SystemReportView._refresh_core_rows",
        "logical_cores": count,
        "iterations": ITERATIONS,
        "widget_count_per_refresh": widget_count,
        "median_ms": round(statistics.median(samples), 4),
        "p95_ms": round(percentile(samples, 0.95), 4),
        "max_ms": round(max(samples), 4),
        "cpu_ms_per_refresh": round(1000 * elapsed_cpu / ITERATIONS, 4),
        "wall_ms_per_refresh": round(1000 * elapsed_wall / ITERATIONS, 4),
    }
    view.destroy()
    return result


if __name__ == "__main__":
    print(json.dumps({
        "benchmark": "issue-860 GTK row rebuild baseline",
        "python": platform.python_version(),
        "note": "Synthetic CPU cores; excludes rendering/layout/real sensor reads",
        "results": [bench(n) for n in COUNTS],
    }, sort_keys=True))
