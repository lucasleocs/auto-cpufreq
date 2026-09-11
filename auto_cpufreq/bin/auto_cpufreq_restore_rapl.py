#!/usr/bin/env python3

from auto_cpufreq.modules.intel_rapl import IntelRaplController, RaplStateError


def main() -> int:
    """Restore only RAPL limits still provably owned by auto-cpufreq."""
    try:
        results = IntelRaplController().restore_owned()
    except RaplStateError as exc:
        print(f"Intel RAPL restore failed: {exc}")
        return 1

    failed = False
    for result in results:
        if result.message:
            print(result.message)
        if result.action == "restore-failed":
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
