"""Service-only, independent RAPL restoration entry point.

No daemon, config notifier, GTK, governor or HWP modules are imported.
A journal is authoritative about *which constraints to inspect*, never
permission to overwrite an externally changed firmware value.
"""
import os
import sys

from auto_cpufreq.modules.intel_rapl import (
    IntelRaplController,
    RaplStateError,
)


def main() -> int:
    """Exit zero only if no unresolved auto-cpufreq RAPL ownership remains."""
    if os.geteuid() != 0:
        print("Intel RAPL restore requires root", file=sys.stderr)
        return 1

    controller = IntelRaplController()
    try:
        results = controller.restore_owned()
        # The helper's exit status cannot be based solely on action strings:
        # an unknown future result must not hide surviving journal records.
        remaining = controller.state_store.load()
    except (RaplStateError, OSError) as exc:
        print(f"Intel RAPL restore failed: {exc}", file=sys.stderr)
        return 1

    for result in results:
        if result.message:
            print(f"Intel RAPL [{result.action}]: {result.message}",
                  file=sys.stderr)

    if remaining:
        print(
            f"Intel RAPL restore incomplete: {len(remaining)} unresolved "
            "constraint(s) remain in the recovery journal",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
