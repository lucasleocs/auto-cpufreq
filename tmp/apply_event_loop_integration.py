#!/usr/bin/env python3
from pathlib import Path


cli_path = Path("auto_cpufreq/bin/auto_cpufreq.py")
text = cli_path.read_text(encoding="utf-8")

old_import = (
    "from auto_cpufreq.config.config import config as conf, find_config_file\n"
    "from auto_cpufreq.core import *\n"
)
new_import = (
    "from auto_cpufreq.config.config import config as conf, find_config_file\n"
    "from auto_cpufreq.core import *\n"
    "from auto_cpufreq.modules.daemon_scheduler import create_daemon_scheduler\n"
)
assert text.count(old_import) == 1, "unexpected CLI import context"
text = text.replace(old_import, new_import, 1)

old_daemon = '''            start_battery_daemon()
            conf.notifier.start()
            while True:
                try:
                    footer()
                    gov_check()
                    cpufreqctl()
                    print_system_report()
                    set_autofreq()
                    countdown(2)
                except KeyboardInterrupt: break
            conf.notifier.stop()
'''
new_daemon = '''            start_battery_daemon()
            scheduler = create_daemon_scheduler(get_policy_backend())
            conf.set_change_callback(scheduler.notify_config_change)
            conf.notifier.start()

            if scheduler.event_source_error is not None:
                print(
                    "Warning: power_supply event monitoring is unavailable: "
                    f"{scheduler.event_source_error}"
                )
            if scheduler.config_source_error is not None:
                print(
                    "Warning: config event wakeup is unavailable: "
                    f"{scheduler.config_source_error}"
                )
            if scheduler.using_periodic_fallback:
                print(
                    "Warning: using periodic policy fallback because an "
                    "event-driven wakeup source is unavailable."
                )

            try:
                while True:
                    footer()
                    gov_check()
                    cpufreqctl()
                    print_system_report()
                    set_autofreq()
                    scheduler.wait_for_policy_trigger()
            except KeyboardInterrupt:
                pass
            finally:
                conf.set_change_callback(None)
                scheduler.close()
                conf.notifier.stop()
'''
assert text.count(old_daemon) == 1, "unexpected daemon loop context"
text = text.replace(old_daemon, new_daemon, 1)
cli_path.write_text(text, encoding="utf-8")

service_path = Path("scripts/auto-cpufreq.service")
service = service_path.read_text(encoding="utf-8")
old_service = "Restart=on-failure\n"
new_service = "Restart=on-failure\nWatchdogSec=30s\n"
assert service.count(old_service) == 1, "unexpected service context"
service = service.replace(old_service, new_service, 1)
service_path.write_text(service, encoding="utf-8")
