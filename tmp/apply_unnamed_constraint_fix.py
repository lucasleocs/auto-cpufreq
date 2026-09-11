from pathlib import Path

path = Path("auto_cpufreq/modules/intel_power.py")
text = path.read_text()
old = '''    @staticmethod\n    def _constraint_indexes(zone_path: Path) -> tuple[int, ...]:\n        indexes: set[int] = set()\n        try:\n            for path in zone_path.glob("constraint_*_name"):\n                suffix = path.name.removeprefix("constraint_").removesuffix("_name")\n                if suffix.isdigit():\n                    indexes.add(int(suffix))\n        except OSError:\n            return ()\n        return tuple(sorted(indexes))\n'''
new = '''    @staticmethod\n    def _constraint_indexes(zone_path: Path) -> tuple[int, ...]:\n        indexes: set[int] = set()\n        try:\n            for path in zone_path.glob("constraint_*_*"):\n                suffix = path.name.removeprefix("constraint_")\n                index_text, separator, _attribute = suffix.partition("_")\n                if separator and index_text.isdigit():\n                    indexes.add(int(index_text))\n        except OSError:\n            return ()\n        return tuple(sorted(indexes))\n'''
if old not in text:
    raise SystemExit("expected _constraint_indexes block not found")
path.write_text(text.replace(old, new, 1))
print("unnamed constraint fix applied")
