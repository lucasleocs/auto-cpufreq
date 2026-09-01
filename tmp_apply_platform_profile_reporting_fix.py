from pathlib import Path


def replace_once(path: str, old: str, new: str):
    file_path = Path(path)
    text = file_path.read_text()
    if old not in text:
        raise SystemExit(f"expected source block not found in {path}")
    file_path.write_text(text.replace(old, new, 1))


replace_once(
    "auto_cpufreq/modules/system_info.py",
    '''def format_platform_profile_summary(\n    snapshot: PlatformProfileSnapshot,\n) -> list[str]:\n''',
    '''def format_platform_profile_choices(\n    snapshot: PlatformProfileSnapshot,\n    label: str = "Available profiles",\n) -> tuple[str, str]:\n    """Format a Platform Profile choices label and value without conflating unknown with empty."""\n    if not snapshot.choices_known:\n        return label, "Could not be determined"\n\n    count = len(snapshot.available_profiles)\n    value = (\n        ", ".join(snapshot.available_profiles)\n        if snapshot.available_profiles\n        else "None available"\n    )\n    return f"{label} ({count})", value\n\n\ndef format_platform_profile_summary(\n    snapshot: PlatformProfileSnapshot,\n) -> list[str]:\n''',
)

replace_once(
    "auto_cpufreq/modules/system_info.py",
    '''    if snapshot.available_profiles:\n        available = ", ".join(snapshot.available_profiles)\n    elif not snapshot.choices_known:\n        available = "Could not be determined"\n    else:\n        available = "None available"\n    lines.append(\n        f"Available profiles ({len(snapshot.available_profiles)}): {available}"\n    )\n''',
    '''    available_label, available = format_platform_profile_choices(snapshot)\n    lines.append(f"{available_label}: {available}")\n''',
)

replace_once(
    "auto_cpufreq/modules/system_info.py",
    '''    profiles_text = (\n        ", ".join(platform_state.available_profiles)\n        if platform_state.available_profiles\n        else "None reported"\n    )\n    lines.append(\n        f"Available platform profiles ({len(platform_state.available_profiles)}): "\n        f"{profiles_text}"\n    )\n''',
    '''    profiles_label, profiles_text = format_platform_profile_choices(\n        platform_state,\n        label="Available platform profiles",\n    )\n    lines.append(f"{profiles_label}: {profiles_text}")\n''',
)

replace_once(
    "auto_cpufreq/gui/app.py",
    '''from auto_cpufreq.modules.system_info import system_info\n''',
    '''from auto_cpufreq.modules.system_info import (\n    format_platform_profile_choices,\n    system_info,\n)\n''',
)

replace_once(
    "auto_cpufreq/gui/app.py",
    '''            profile_count = len(platform_state.available_profiles)\n            self.platform_names["available"].set_text(\n                f"Available profiles ({profile_count})"\n            )\n            if platform_state.available_profiles:\n                available_text = ", ".join(platform_state.available_profiles)\n            elif not platform_state.choices_known:\n                available_text = "Could not be determined"\n            else:\n                available_text = "None available"\n            self.platform_values["available"].set_text(available_text)\n''',
    '''            available_label, available_text = format_platform_profile_choices(\n                platform_state\n            )\n            self.platform_names["available"].set_text(available_label)\n            self.platform_values["available"].set_text(available_text)\n''',
)
