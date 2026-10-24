import contextlib
import fcntl
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def run_child():
    sys.path.insert(0, str(ROOT))
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    if os.environ.get("ACF_IGNORE_HUP") == "1":
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    info = types.ModuleType("auto_cpufreq.modules.system_info")
    info.SystemReport = object
    info.battery_power_label = lambda _battery: "Battery power"
    info.format_platform_profile_summary = lambda _profile: []
    info.system_info = types.SimpleNamespace(generate_system_report=lambda: object())
    sys.modules[info.__name__] = info

    configuration = types.ModuleType("auto_cpufreq.config.config")
    configuration.config = types.SimpleNamespace(path="/dev/null")
    sys.modules[configuration.__name__] = configuration

    from auto_cpufreq.modules.system_monitor import SystemMonitor, ViewType

    monitor = SystemMonitor(
        type=ViewType.STATS,
        diagnostic_log=os.environ.get("ACF_DIAG") == "1",
    )
    monitor.format_system_info = lambda *_args: None
    original_update = monitor.update
    ready_fd = int(os.environ["ACF_READY_FD"])
    initialized = False

    def ready_update(loop, user_data):
        nonlocal initialized
        if not initialized:
            initialized = True
            os.write(
                ready_fd,
                f"{os.tcgetpgrp(0)}:{os.getpgrp()}".encode("ascii"),
            )
            os.close(ready_fd)
        return original_update(loop, user_data)

    monitor.update = ready_update
    monitor.run()


class TerminalProcess:
    def __init__(self, ignore_hup=False, diagnostic=False):
        self._logs = tempfile.TemporaryDirectory()
        self.master, slave = pty.openpty()
        self.read_fd, write_fd = os.pipe()
        env = os.environ.copy()
        env["ACF_READY_FD"] = str(write_fd)
        env["XDG_STATE_HOME"] = self._logs.name
        if diagnostic:
            env["ACF_DIAG"] = "1"
        if ignore_hup:
            env["ACF_IGNORE_HUP"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--child"],
            cwd=ROOT,
            env=env,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            pass_fds=(write_fd,),
            start_new_session=True,
        )
        os.close(slave)
        os.close(write_fd)
        self.output = bytearray()

    def wait_ready(self, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"Child exited before readiness: {self.proc.returncode}, "
                    f"output={self.drain()[-2500:]!r}"
                )
            readable, _, _ = select.select(
                [self.read_fd, self.master], [], [], 0.15
            )
            if self.read_fd in readable:
                data = os.read(self.read_fd, 128)
                if not data:
                    raise AssertionError(
                        f"Child failed to initialize: {self.drain()[-2500:]!r}"
                    )
                foreground, group = map(int, data.decode("ascii").split(":"))
                if foreground != group or group != self.proc.pid:
                    raise AssertionError(
                        f"PTY process group mismatch: {foreground}, {group}"
                    )
                return
            if self.master in readable:
                self.drain()
        raise AssertionError(f"Child startup timeout: {self.drain()[-2500:]!r}")

    def drain(self):
        if self.master is None:
            return bytes(self.output)
        while True:
            readable, _, _ = select.select([self.master], [], [], 0)
            if not readable:
                break
            try:
                data = os.read(self.master, 65536)
            except OSError:
                break
            if not data:
                break
            self.output.extend(data)
        return bytes(self.output)

    def send(self, data):
        os.write(self.master, data)

    def close_terminal(self):
        if self.master is not None:
            os.close(self.master)
            self.master = None

    def wait_exit(self, timeout=4):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def cpu_seconds(self):
        stat = Path(f"/proc/{self.proc.pid}/stat")
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            ticks = int(fields[11]) + int(fields[12])
            return ticks / os.sysconf("SC_CLK_TCK")
        except (FileNotFoundError, ProcessLookupError):
            return 0.0

    def cleanup(self):
        if self.proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGTERM)
            if self.wait_exit(timeout=2) is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=3)
        self.close_terminal()
        os.close(self.read_fd)
        self._logs.cleanup()


class StatsLifecycleTests(unittest.TestCase):
    @contextlib.contextmanager
    def terminal(self, ignore_hup=False, diagnostic=False):
        child = TerminalProcess(
            ignore_hup=ignore_hup, diagnostic=diagnostic
        )
        try:
            child.wait_ready()
            yield child
        finally:
            child.cleanup()

    def test_diagnostic_logs_are_private_and_end_on_quit(self):
        with self.terminal(diagnostic=True) as child:
            child.send(b"q")
            self.assertEqual(child.wait_exit(), 0, child.drain()[-2500:])
            logs = list(
                (Path(child._logs.name) / "auto-cpufreq").glob("stats-*.log")
            )
            self.assertEqual(len(logs), 1)
            data = logs[0].read_text(encoding="utf-8")
            self.assertIn("UI started", data)
            self.assertIn("UI stopped", data)
            self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)

    def test_quit_key(self):
        with self.terminal() as child:
            child.send(b"q")
            self.assertEqual(child.wait_exit(), 0, child.drain()[-2500:])

    def test_ctrl_c(self):
        with self.terminal() as child:
            child.send(b"\x03")
            self.assertEqual(child.wait_exit(), 0, child.drain()[-2500:])

    def test_explicit_sighup(self):
        with self.terminal() as child:
            os.kill(child.proc.pid, signal.SIGHUP)
            self.assertEqual(child.wait_exit(), -signal.SIGHUP)

    def test_close_controlling_terminal(self):
        with self.terminal() as child:
            child.close_terminal()
            result = child.wait_exit(timeout=4)
            self.assertIsNotNone(result, "Monitor survived PTY hangup")

    def test_idle_cpu_after_multiple_refreshes(self):
        with self.terminal() as child:
            start_cpu = child.cpu_seconds()
            start = time.monotonic()
            time.sleep(5)
            self.assertIsNone(child.proc.poll(), child.drain()[-2500:])
            elapsed = time.monotonic() - start
            ratio = (child.cpu_seconds() - start_cpu) / elapsed
            print(f"active-terminal: CPU/core={ratio:.3f}")
            self.assertLess(ratio, 0.5, "Monitor spins during ordinary refresh")
            child.send(b"q")
            self.assertEqual(child.wait_exit(), 0, child.drain()[-2500:])

    def test_hangup_after_multiple_refreshes(self):
        with self.terminal() as child:
            time.sleep(5)
            self.assertIsNone(child.proc.poll(), child.drain()[-2500:])
            child.close_terminal()
            self.assertIsNotNone(
                child.wait_exit(timeout=4),
                "Monitor survived PTY hangup after refresh cycles",
            )

    def test_ignored_hup_does_not_spin(self):
        with self.terminal(ignore_hup=True) as child:
            child.close_terminal()
            start_cpu = child.cpu_seconds()
            start = time.monotonic()
            result = child.wait_exit(timeout=1.5)
            if result is None:
                elapsed = time.monotonic() - start
                used = child.cpu_seconds() - start_cpu
                ratio = used / max(elapsed, 0.01)
                print(f"ignored-SIGHUP: process alive, CPU/core={ratio:.3f}")
                self.assertLess(
                    ratio, 0.75,
                    "Surviving monitor has sustained near-core CPU usage",
                )
            else:
                print(f"ignored-SIGHUP: process exited with code {result}")


if __name__ == "__main__":
    if "--child" in sys.argv:
        run_child()
    else:
        unittest.main(verbosity=2)
