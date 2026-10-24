import argparse
import csv
import datetime
import os
import sys
import time
from pathlib import Path


CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


def cpu_ticks(stat_text):
    fields = stat_text.rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])


def task_ticks(pid):
    values = {}
    try:
        tasks = Path(f"/proc/{pid}/task").iterdir()
        for task in tasks:
            try:
                values[int(task.name)] = cpu_ticks(
                    (task / "stat").read_text()
                )
            except (FileNotFoundError, PermissionError, ValueError, IndexError):
                continue
    except (FileNotFoundError, PermissionError):
        pass
    return values


def snapshot(pid):
    path = Path(f"/proc/{pid}")
    fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
    status = (path / "status").read_text().splitlines()
    metadata = {}
    for line in status:
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key] = value.strip()

    try:
        file_descriptors = len(list((path / "fd").iterdir()))
    except (FileNotFoundError, PermissionError):
        file_descriptors = None

    rss_kb = metadata.get("VmRSS", "0 kB").split()[0]
    return {
        "time": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "monotonic": time.monotonic(),
        "ticks": int(fields[11]) + int(fields[12]),
        "rss_mib": round(int(rss_kb) / 1024, 2),
        "threads": int(metadata.get("Threads", "0")),
        "fds": file_descriptors,
        "state": metadata.get("State", ""),
        "voluntary_switches": int(
            metadata.get("voluntary_ctxt_switches", "0")
        ),
        "involuntary_switches": int(
            metadata.get("nonvoluntary_ctxt_switches", "0")
        ),
        "task_ticks": task_ticks(pid),
    }


def calculate(previous, current):
    elapsed = current["monotonic"] - previous["monotonic"]
    if elapsed <= 0:
        raise ValueError("Nonpositive sample interval")
    cpu_percent = 100 * (
        current["ticks"] - previous["ticks"]
    ) / CLOCK_TICKS / elapsed
    per_thread = {
        tid: 100 * (ticks - previous["task_ticks"][tid])
        / CLOCK_TICKS / elapsed
        for tid, ticks in current["task_ticks"].items()
        if tid in previous["task_ticks"]
    }
    hottest_tid = max(per_thread, key=per_thread.get) if per_thread else ""
    return {
        "timestamp": current["time"],
        "pid": current["pid"],
        "cpu_percent_one_core": round(cpu_percent, 2),
        "hottest_tid": hottest_tid,
        "hottest_tid_cpu_percent": round(
            per_thread[hottest_tid], 2
        ) if hottest_tid != "" else "",
        "rss_mib": current["rss_mib"],
        "threads": current["threads"],
        "fds": current["fds"] if current["fds"] is not None else "",
        "state": current["state"],
        "voluntary_switches": current["voluntary_switches"],
        "involuntary_switches": current["involuntary_switches"],
    }


def sample(pid, interval, samples, output):
    previous = snapshot(pid)
    columns = (
        "timestamp", "pid", "cpu_percent_one_core",
        "hottest_tid", "hottest_tid_cpu_percent",
        "rss_mib", "threads", "fds", "state",
        "voluntary_switches", "involuntary_switches",
    )
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    output.flush()
    for _ in range(samples):
        time.sleep(interval)
        try:
            current = snapshot(pid)
        except (FileNotFoundError, ProcessLookupError):
            print(f"Process {pid} exited during sampling", file=sys.stderr)
            break
        current["pid"] = pid
        row = calculate(previous, current)
        writer.writerow(row)
        output.flush()
        previous = current


def main():
    parser = argparse.ArgumentParser(
        description="Read-only Linux process and thread CPU sampler"
    )
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.pid <= 0 or args.interval < 0.1 or args.samples < 1:
        parser.error("pid must be positive, interval >= 0.1, samples >= 1")
    if args.output:
        with open(args.output, "w", newline="") as output:
            sample(args.pid, args.interval, args.samples, output)
    else:
        sample(args.pid, args.interval, args.samples, sys.stdout)


if __name__ == "__main__":
    main()
