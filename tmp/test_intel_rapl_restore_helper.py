from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from auto_cpufreq.modules.intel_rapl import RaplStateError
from auto_cpufreq.bin import auto_cpufreq_restore_rapl as helper


class FakeController:
    def __init__(self, results=(), error=None):
        self.results = results
        self.error = error
        self.calls = 0

    def restore_owned(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.results


def run_helper(controller):
    original = helper.IntelRaplController
    helper.IntelRaplController = lambda: controller
    output = StringIO()
    try:
        with redirect_stdout(output):
            code = helper.main()
    finally:
        helper.IntelRaplController = original
    return code, output.getvalue()


def test_success_and_failure_exit_codes():
    controller = FakeController(
        results=(
            SimpleNamespace(action="restored", message="restored long_term"),
            SimpleNamespace(action="retired-drift", message="drift skipped"),
        )
    )
    code, output = run_helper(controller)
    assert controller.calls == 1
    assert code == 0
    assert "restored long_term" in output
    assert "drift skipped" in output

    failed = FakeController(
        results=(
            SimpleNamespace(action="restore-failed", message="permission denied"),
        )
    )
    code, output = run_helper(failed)
    assert code == 1
    assert "permission denied" in output

    state_error = FakeController(error=RaplStateError("state unavailable"))
    code, output = run_helper(state_error)
    assert code == 1
    assert "state unavailable" in output


def test_helper_dependency_surface():
    source_path = Path(helper.__file__)
    source = source_path.read_text(encoding="utf-8")
    assert "auto_cpufreq.core" not in source
    assert "auto_cpufreq.config" not in source
    assert "pyinotify" not in source
    assert "IntelRaplController" in source


def test_packaging_and_systemd_lifecycle():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert (
        'auto-cpufreq-restore-rapl = '
        '"auto_cpufreq.bin.auto_cpufreq_restore_rapl:main"'
    ) in pyproject

    service = Path("scripts/auto-cpufreq.service").read_text(encoding="utf-8")
    required = (
        "RuntimeDirectory=auto-cpufreq",
        "RuntimeDirectoryMode=0700",
        "Environment=AUTO_CPUFREQ_RAPL_FAILSAFE=1",
        "ExecStopPost=/opt/auto-cpufreq/venv/bin/auto-cpufreq-restore-rapl",
        "Restart=on-failure",
        "WatchdogSec=30s",
    )
    for line in required:
        assert line in service, line


def main():
    test_success_and_failure_exit_codes()
    test_helper_dependency_surface()
    test_packaging_and_systemd_lifecycle()
    print("Intel RAPL restore helper checks passed")


if __name__ == "__main__":
    main()
