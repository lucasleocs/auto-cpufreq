from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one patch anchor, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "auto_cpufreq/modules/policy.py",
    '''@dataclass(frozen=True)\nclass ModernIntelActions:\n    apply: Callable[[PowerSource], None]\n    monitor: Callable[[PowerSource], None]\n''',
    '''@dataclass(frozen=True)\nclass ModernIntelActions:\n    apply: Callable[[PowerSource], None]\n    monitor: Callable[[PowerSource], None]\n    apply_power_envelope: Optional[Callable[[PowerSource], None]] = None\n''',
)

replace_once(
    "auto_cpufreq/modules/policy.py",
    '''    def monitor(self, source: PowerSource) -> None:\n        if source is PowerSource.CHARGER:\n            self._actions.monitor_charger()\n        else:\n            self._actions.monitor_battery()\n\n\nclass ModernIntelHwpPolicy(LegacyPolicy):\n''',
    '''    def monitor(self, source: PowerSource) -> None:\n        if source is PowerSource.CHARGER:\n            self._actions.monitor_charger()\n        else:\n            self._actions.monitor_battery()\n\n    def apply_power_envelope(self, source: PowerSource) -> None:\n        """Legacy policy never controls Intel RAPL envelopes."""\n        return None\n\n\nclass ModernIntelHwpPolicy(LegacyPolicy):\n''',
)

replace_once(
    "auto_cpufreq/modules/policy.py",
    '''    def monitor(self, source: PowerSource) -> None:\n        if self._modern_actions is None:\n            super().monitor(source)\n            return\n        self._modern_actions.monitor(source)\n\n\ndef modern_intel_hwp_eligible''',
    '''    def monitor(self, source: PowerSource) -> None:\n        if self._modern_actions is None:\n            super().monitor(source)\n            return\n        self._modern_actions.monitor(source)\n\n    def apply_power_envelope(self, source: PowerSource) -> None:\n        if self._modern_actions is None:\n            return\n        callback = self._modern_actions.apply_power_envelope\n        if callback is not None:\n            callback(source)\n\n\ndef modern_intel_hwp_eligible''',
)

replace_once(
    "auto_cpufreq/core.py",
    '''from auto_cpufreq.modules.intel_power import IntelPowerDiscovery\n''',
    '''from auto_cpufreq.modules.intel_power import IntelPowerDiscovery\nfrom auto_cpufreq.modules.intel_rapl import (\n    IntelRaplController,\n    RaplConfigError,\n    RaplStateError,\n    parse_rapl_policy_config,\n)\n''',
)

rapl_function = '''def apply_modern_intel_rapl(source):\n    """Apply optional package RAPL envelopes only in a failsafe daemon."""\n    conf = config.get_config()\n    try:\n        policy = parse_rapl_policy_config(conf, source.value)\n    except RaplConfigError as exc:\n        print(f"Warning: Intel RAPL configuration ignored: {exc}")\n        return\n\n    failsafe_available = os.environ.get("AUTO_CPUFREQ_RAPL_FAILSAFE") == "1"\n\n    if not policy.enabled:\n        if not failsafe_available:\n            return\n        try:\n            results = IntelRaplController().restore_owned()\n        except RaplStateError as exc:\n            print(f"Warning: Intel RAPL ownership state unavailable: {exc}")\n            return\n    else:\n        if not failsafe_available:\n            print(\n                "Warning: Intel RAPL envelopes are enabled, but the daemon "\n                "failsafe is unavailable; RAPL writes were skipped."\n            )\n            return\n        try:\n            results = IntelRaplController().apply(policy.targets)\n        except RaplStateError as exc:\n            print(f"Warning: Intel RAPL ownership state unavailable: {exc}")\n            return\n\n    for result in results:\n        if result.message:\n            print(result.message)\n\n\n'''
replace_once(
    "auto_cpufreq/core.py",
    '''def get_policy_backend():\n''',
    rapl_function + '''def get_policy_backend():\n''',
)

replace_once(
    "auto_cpufreq/core.py",
    '''            modern_actions=ModernIntelActions(\n                apply=set_modern_intel_hwp,\n                monitor=mon_modern_intel_hwp,\n            ),\n''',
    '''            modern_actions=ModernIntelActions(\n                apply=set_modern_intel_hwp,\n                monitor=mon_modern_intel_hwp,\n                apply_power_envelope=apply_modern_intel_rapl,\n            ),\n''',
)

replace_once(
    "auto_cpufreq/bin/auto_cpufreq.py",
    '''def _daemon_policy_cycle(source=None):\n    footer()\n    gov_check()\n    cpufreqctl()\n    print_system_report()\n    return set_autofreq(source)\n''',
    '''def _daemon_policy_cycle(source=None):\n    footer()\n    gov_check()\n    cpufreqctl()\n    print_system_report()\n    source = set_autofreq(source)\n    get_policy_backend().apply_power_envelope(source)\n    return source\n''',
)

print("Task 5 production patch applied")
