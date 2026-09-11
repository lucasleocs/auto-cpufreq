#!/usr/bin/env python3
from pathlib import Path


CORE = Path("auto_cpufreq/core.py")
CLI = Path("auto_cpufreq/bin/auto_cpufreq.py")


core = CORE.read_text(encoding="utf-8")

needle = "def set_autofreq():\n"
assert core.count(needle) == 1, core.count(needle)
core = core.replace(
    needle,
    "def get_power_source():\n"
    "    return PowerSource.CHARGER if charging() else PowerSource.BATTERY\n\n\n"
    "def set_autofreq(source=None):\n",
    1,
)

function_start = core.index("def set_autofreq(source=None):\n")
next_function = core.index("\n\ndef mon_autofreq", function_start)
start_marker = "    # determine which power profile should be used\n"
end_marker = "    get_policy_backend().apply(source)"
start = core.index(start_marker, function_start, next_function)
end_start = core.index(end_marker, start, next_function)
end = end_start + len(end_marker)

replacement = (
    "    # determine which power profile should be used\n"
    "    if source is None:\n"
    "        source = get_power_source()\n\n"
    "    if source is PowerSource.CHARGER:\n"
    "        print(\"Battery is: charging\\n\")\n"
    "    else:\n"
    "        print(\"Battery is: discharging\\n\")\n\n"
    "    get_policy_backend().apply(source)\n"
    "    return source"
)
core = core[:start] + replacement + core[end:]
CORE.write_text(core, encoding="utf-8")


cli = CLI.read_text(encoding="utf-8")
old_import = (
    "from auto_cpufreq.modules.daemon_scheduler import create_daemon_scheduler\n"
)
new_import = (
    "from auto_cpufreq.modules.daemon_scheduler import (\n"
    "    PolicyTrigger,\n"
    "    create_daemon_scheduler,\n"
    "    should_reapply_policy,\n"
    ")\n"
)
assert cli.count(old_import) == 1, cli.count(old_import)
cli = cli.replace(old_import, new_import, 1)

main_marker = "\n\n@click.command()\n"
assert cli.count(main_marker) == 1, cli.count(main_marker)
helpers = '''\n\ndef _daemon_policy_cycle(source=None):\n    footer()\n    gov_check()\n    cpufreqctl()\n    print_system_report()\n    return set_autofreq(source)\n\n\ndef _run_daemon_policy_loop(scheduler):\n    last_source = _daemon_policy_cycle()\n\n    while True:\n        triggers = scheduler.wait_for_policy_trigger()\n        current_source = get_power_source()\n\n        if not should_reapply_policy(\n            triggers,\n            last_source,\n            current_source,\n        ):\n            continue\n\n        last_source = _daemon_policy_cycle(current_source)\n'''
cli = cli.replace(main_marker, helpers + main_marker, 1)

old_loop = '''            try:\n                while True:\n                    footer()\n                    gov_check()\n                    cpufreqctl()\n                    print_system_report()\n                    set_autofreq()\n                    scheduler.wait_for_policy_trigger()\n            except KeyboardInterrupt:\n                pass\n'''
new_loop = '''            try:\n                _run_daemon_policy_loop(scheduler)\n            except KeyboardInterrupt:\n                pass\n'''
assert cli.count(old_loop) == 1, cli.count(old_loop)
cli = cli.replace(old_loop, new_loop, 1)
CLI.write_text(cli, encoding="utf-8")
