"""Nonprivileged tests for Live session rollback, threads and CLI wiring."""
import ast
import io
import sys
import threading
import time
import unittest
from pathlib import Path

from auto_cpufreq.modules.live_session import LiveModeSession
from auto_cpufreq.modules.thread_output import ThreadSelectiveStdout


ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "auto_cpufreq" / "bin" / "auto_cpufreq.py"


class ThreadOutputTests(unittest.TestCase):
    def test_silencing_one_thread_does_not_mute_other_threads(self):
        stream = io.StringIO()
        output = ThreadSelectiveStdout(stream)
        entered, release = threading.Event(), threading.Event()

        def worker():
            with output.silence_current_thread():
                output.write("hidden")
                entered.set()
                release.wait(2)
                output.write("hidden")

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            output.write("visible")
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(stream.getvalue(), "visible")

    def test_nested_suppression_restores_after_exception(self):
        output = ThreadSelectiveStdout(io.StringIO())
        with self.assertRaises(RuntimeError):
            with output.silence_current_thread():
                with output.silence_current_thread():
                    output.write("hidden")
                    raise RuntimeError("intentional")
        output.write("visible")
        self.assertEqual(output._stream.getvalue(), "visible")


class LiveSessionTests(unittest.TestCase):
    def make_session(self, policy=None, service_steps=None, watch_start=None,
                     watch_stop=None):
        calls = []
        steps = service_steps if service_steps is not None else [
            ("service", lambda: calls.append("stop-service"),
             lambda: calls.append("restore-service"))
        ]
        session = LiveModeSession(
            policy=policy or (lambda: None),
            service_steps=steps,
            watch_start=watch_start or (lambda: calls.append("watch-start")),
            watch_stop=watch_stop or (lambda: calls.append("watch-stop")),
            tick_interval=0.01,
        )
        self.addCleanup(lambda: self.safe_close(session))
        return session, calls

    @staticmethod
    def safe_close(session):
        try:
            session.close(timeout=0.5)
        except RuntimeError:
            pass

    def test_start_and_stop_restore_once_in_reverse_order(self):
        calls = []
        session, _ = self.make_session(service_steps=[
            ("a", lambda: calls.append("stop-a"), lambda: calls.append("restore-a")),
            ("b", lambda: calls.append("stop-b"), lambda: calls.append("restore-b")),
        ])
        previous = sys.stdout
        session.prepare()
        session.start()
        self.assertEqual(session.status, "running")
        session.close()
        session.close()
        self.assertEqual(session.status, "stopped")
        self.assertIs(sys.stdout, previous)
        self.assertEqual(calls, [
            "stop-a", "stop-b", "watch-start", "watch-stop",
            "restore-b", "restore-a",
        ])

    def test_prepare_failure_rolls_back_completed_and_partial_stops(self):
        calls = []

        def broken_stop():
            calls.append("stop-attempt")
            raise RuntimeError("partial stop")

        session, _ = self.make_session(service_steps=[
            ("service", broken_stop, lambda: calls.append("restore")),
        ])
        with self.assertRaisesRegex(RuntimeError, "partial stop"):
            session.prepare()
        session.close()
        self.assertEqual(calls, ["stop-attempt", "restore"])
        self.assertEqual(session.status, "stopped")

    def test_watcher_start_failure_triggers_watch_stop_and_service_restore(self):
        calls = []

        def fail_watch():
            calls.append("start-watch")
            raise OSError("watch setup failed")

        session, _ = self.make_session(
            watch_start=fail_watch,
            watch_stop=lambda: calls.append("stop-watch"),
            service_steps=[
                ("service", lambda: calls.append("stop-service"),
                 lambda: calls.append("restore-service")),
            ],
        )
        session.prepare()
        with self.assertRaisesRegex(OSError, "watch setup failed"):
            session.start()
        session.close()
        self.assertEqual(calls, [
            "stop-service", "start-watch", "stop-watch", "restore-service",
        ])

    def test_blocked_worker_defers_restore_and_retry_succeeds(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocked_policy():
            entered.set()
            release.wait(2)

        session, _ = self.make_session(
            policy=blocked_policy,
            service_steps=[
                ("service", lambda: calls.append("stop"),
                 lambda: calls.append("restore")),
            ]
        )
        session.prepare()
        session.start()
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaisesRegex(RuntimeError, "restoration deferred"):
                session.close(timeout=0.01)
            self.assertEqual(calls, ["stop"])
            self.assertEqual(session.status, "stopping")
        finally:
            release.set()
        session.close(timeout=1)
        self.assertEqual(calls, ["stop", "restore"])
        self.assertEqual(session.status, "stopped")

    def test_restore_failure_is_retryable_without_repeating_other_steps(self):
        calls = []

        def flaky_restore():
            calls.append("restore-a")
            if calls.count("restore-a") == 1:
                raise RuntimeError("cannot restore yet")

        session, _ = self.make_session(service_steps=[
            ("a", lambda: calls.append("stop-a"), flaky_restore),
            ("b", lambda: calls.append("stop-b"), lambda: calls.append("restore-b")),
        ])
        session.prepare()
        with self.assertRaisesRegex(RuntimeError, "cannot restore yet"):
            session.close()
        self.assertEqual(calls.count("restore-b"), 1)
        session.close()
        self.assertEqual(calls.count("restore-a"), 2)
        self.assertEqual(calls.count("restore-b"), 1)
        self.assertEqual(session.status, "stopped")

    def test_worker_failure_visible_even_when_ui_remains_open(self):
        def fail_policy():
            raise OSError("simulated policy fault")

        session, _ = self.make_session(policy=fail_policy)
        session.prepare()
        session.start()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and session.policy_failure is None:
            time.sleep(0.01)
        self.assertIsNotNone(session.policy_failure)
        self.assertIn("failed: simulated policy fault", session.status)
        session.close()


class ReadOnlyModeWiringTests(unittest.TestCase):
    def branches(self):
        source = CLI.read_text(encoding="utf-8")
        main = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        return {
            node.test.id: ast.unparse(node.body)
            for node in ast.walk(main)
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
            and node.test.id in {"monitor", "live", "daemon", "stats", "debug"}
        }

    def test_watcher_only_in_running_policy_and_monitor_modes(self):
        branches = self.branches()
        self.assertIn("conf.notifier.start()", branches["monitor"])
        self.assertIn("conf.notifier.start()", branches["daemon"])
        self.assertIn("session.start()", branches["live"])
        for name in ("stats", "debug"):
            self.assertNotIn("notifier.start()", branches[name])
            self.assertNotIn("session.start()", branches[name])

    def test_debug_does_not_install_helper_and_live_uses_runtime(self):
        branches = self.branches()
        self.assertNotIn("cpufreqctl()", branches["debug"])
        self.assertIn("session.prepare()", branches["live"])
        self.assertIn("session.close()", branches["live"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
