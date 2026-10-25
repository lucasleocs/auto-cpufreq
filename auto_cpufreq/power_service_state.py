"""Conservative systemd state tracking for source-installed power services.

The upstream source daemon manages competing systemd services. Remember their
original state so daemon removal does not blindly enable previously disabled
services. Other packaging methods manage their own services.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from shutil import which


STATE_FILE = Path("/var/lib/auto-cpufreq/power-services.json")
UNITS = ("power-profiles-daemon.service", "tuned.service", "tuned-ppd.service")
# Unsupported unit-file states (linked, generated, alias, runtime-only, etc.)
# are deliberately left alone rather than guessed at.
SUPPORTED_STATES = frozenset(("enabled", "disabled", "static"))


class PowerServiceState:
    def __init__(self, state_file=STATE_FILE, runner=None):
        self.state_file = Path(state_file)
        self.runner = runner or subprocess.run

    def _run(self, *args):
        try:
            return self.runner(
                ["systemctl", *args], capture_output=True, text=True,
            )
        except OSError as error:
            raise RuntimeError(f"systemctl {' '.join(args)}: {error}") from error

    def _command(self, *args):
        result = self._run(*args)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"systemctl {' '.join(args)} failed: {detail}")

    def _inspect(self, unit):
        result = self._run(
            "show", unit,
            "--property=LoadState,ActiveState,UnitFileState", "--no-pager",
        )
        if result.returncode:
            return None
        fields = dict(
            line.split("=", 1) for line in result.stdout.splitlines()
            if "=" in line
        )
        if fields.get("LoadState") not in ("loaded", "masked"):
            return None
        if not fields.get("UnitFileState") or not fields.get("ActiveState"):
            return None
        return {
            "unit_file": fields["UnitFileState"],
            "active": fields["ActiveState"] == "active",
        }

    def _read(self):
        if not self.state_file.exists() and not self.state_file.is_symlink():
            return {}
        if self.state_file.is_symlink():
            raise RuntimeError("Refusing a symlinked power-service state file")
        try:
            with self.state_file.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, ValueError) as error:
            raise RuntimeError(f"Cannot read saved power-service state: {error}") from error
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise RuntimeError("Unsupported power-service state format")
        records = payload.get("services")
        if not isinstance(records, dict):
            raise RuntimeError("Invalid power-service state records")
        for unit, saved in records.items():
            if (unit not in UNITS or not isinstance(saved, dict)
                    or saved.get("unit_file") not in SUPPORTED_STATES
                    or type(saved.get("active")) is not bool
                    or type(saved.get("restoring", False)) is not bool):
                raise RuntimeError("Invalid power-service state entry")
        return records

    def _save(self, records):
        if not records:
            self.state_file.unlink(missing_ok=True)
            return
        directory = self.state_file.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink() or directory.stat().st_uid != os.geteuid():
            raise RuntimeError("Power-service state directory is not trusted")
        os.chmod(directory, 0o700)
        fd, temp_path = tempfile.mkstemp(prefix=".power-services-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "services": records}, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.state_file)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def disable(self):
        """Record original states before masking conflicting systemd units."""
        if which("systemctl") is None:
            return True
        try:
            records = self._read()
            if any(item.get("restoring") for item in records.values()):
                print("A previous power-service restoration is incomplete; resolve it first.")
                return False
            # Snapshot all units before changing any of them: stopping TuneD
            # may also stop tuned-ppd, so later observations could be wrong.
            originals = {}
            for unit in UNITS:
                if unit in records:
                    continue  # Preserve the first installation's snapshot.
                original = self._inspect(unit)
                if not original:
                    continue
                if original["unit_file"] not in SUPPORTED_STATES:
                    print(f"Warning: leaving {unit} unchanged (unsupported unit-file state)")
                    continue
                originals[unit] = original
            if originals:
                records.update(originals)
                self._save(records)  # Durable before any system service change.
            for unit, original in originals.items():
                if original["unit_file"] == "enabled":
                    self._command("disable", unit)
                if original["active"]:
                    self._command("stop", unit)
                self._command("mask", unit)
            return True
        except (RuntimeError, OSError) as error:
            print(f"Unable to manage conflicting power services: {error}")
            self.restore(force=True)  # Undo a partially completed installation.
            return False

    def restore(self, force=False):
        """Undo our masks, without overriding subsequent user modifications."""
        try:
            records = self._read()
        except RuntimeError as error:
            print(f"Unable to restore power services: {error}")
            return False
        success = True
        for unit, original in list(records.items()):
            try:
                current = self._inspect(unit)
                if current is None:
                    raise RuntimeError("service state is no longer readable")
                if not force and not original.get("restoring") and (
                    current["unit_file"] != "masked" or current["active"]
                ):
                    # A different actor modified this unit after installation.
                    raise RuntimeError("service changed since installation; review manually")
                # Persist the restoration phase so interrupted restores can resume
                # even after the mask has already been removed.
                original["restoring"] = True
                self._save(records)
                if current["unit_file"] == "masked":
                    self._command("unmask", unit)
                if original["unit_file"] == "enabled":
                    self._command("enable", unit)
                elif original["unit_file"] == "disabled":
                    self._command("disable", unit)
                if original["active"]:
                    self._command("start", unit)
                records.pop(unit)
                self._save(records)
            except (OSError, RuntimeError) as error:
                success = False
                print(f"Unable to restore {unit}: {error}")
        return success


def disable_conflicting_services():
    return PowerServiceState().disable()


def restore_conflicting_services():
    return PowerServiceState().restore()
