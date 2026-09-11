from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(
            f"expected exactly one match in {path} but found {text.count(old)}"
        )
    path.write_text(text.replace(old, new, 1))


path = Path("auto_cpufreq/modules/system_monitor.py")

replace_once(
    path,
    '''from .system_info import (
    SystemReport,
    format_platform_profile_summary,
    system_info,
)
''',
    '''from .system_info import (
    SystemReport,
    format_intel_power_summary,
    format_platform_profile_summary,
    system_info,
)
''',
)

replace_once(
    path,
    '''    def _collect_report(self):
        try:
            report = system_info.generate_system_report()

            if self.suggestion:
''',
    '''    def _collect_report(self):
        try:
            if self.type == ViewType.STATS:
                report = system_info.generate_system_report(
                    include_intel_power=True,
                    sample_intel_energy=True,
                )
            else:
                report = system_info.generate_system_report()

            if self.suggestion:
''',
)

replace_once(
    path,
    '''        self.right_content.append(aligned_text(""))

        # System Statistics
''',
    '''        self.right_content.append(aligned_text(""))

        if self.type == ViewType.STATS and report.intel_power is not None:
            self.right_content.append(
                urwid.AttrMap(aligned_text("Intel Power"), "header")
            )
            for line in format_intel_power_summary(report.intel_power):
                self.right_content.append(aligned_text(line))
            self.right_content.append(aligned_text(""))

        # System Statistics
''',
)

print("task5 production patch applied")
