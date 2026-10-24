"""Non-privileged stdout and Live-mode lifecycle regressions."""
import ast
import io
import threading
import unittest
from pathlib import Path

from auto_cpufreq.modules.thread_output import ThreadSelectiveStdout


ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "auto_cpufreq" / "bin" / "auto_cpufreq.py"


class ThreadOutputTests(unittest.TestCase):
    def test_silencing_one_thread_does_not_mute_other_threads(self):
        destination = io.StringIO()
        output = ThreadSelectiveStdout(destination)
        entered = threading.Event()
        release = threading.Event()

        def worker():
            with output.silence_current_thread():
                output.write("hidden-1")
                entered.set()
                release.wait(2)
                output.write("hidden-2")

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            output.write("visible")
            output.flush()
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(destination.getvalue(), "visible")

    def test_stdout_is_restored_after_nested_suppression_and_exception(self):
        output = ThreadSelectiveStdout(io.StringIO())
        with self.assertRaisesRegex(RuntimeError, "expected"):
            with output.silence_current_thread():
                output.writelines(["hidden", "lines"])
                with output.silence_current_thread():
                    output.write("hidden")
                raise RuntimeError("expected")
        output.write("visible")
        self.assertEqual(output._stream.getvalue(), "visible")

    def test_proxy_preserves_underlying_stream_capabilities(self):
        output = ThreadSelectiveStdout(io.StringIO())
        self.assertFalse(output.isatty())
        self.assertEqual(output.write("ordinary"), len("ordinary"))
        self.assertEqual(output._stream.getvalue(), "ordinary")


class LiveLifecycleWiringTests(unittest.TestCase):
    def test_stats_never_allocates_watcher_and_running_modes_do(self):
        source = CLI.read_text(encoding="utf-8")
        tree = ast.parse(source)
        main = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "main")
        branches = {
            node.test.id: node
            for node in ast.walk(main)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id in {"monitor", "live", "daemon", "stats"}
        }
        self.assertEqual(set(branches), {"monitor", "live", "daemon", "stats"})
        for mode in ("monitor", "live", "daemon"):
            source_branch = ast.unparse(branches[mode].body)
            self.assertIn("conf.notifier.start()", source_branch)
            self.assertIn("conf.stop_watching()", source_branch)
        self.assertNotIn("conf.notifier.start()", ast.unparse(branches["stats"].body))


    def test_live_mode_uses_stoppable_worker_and_thread_local_output(self):
        source = CLI.read_text(encoding="utf-8")
        module = ast.parse(source)
        self.assertNotIn("sys.stdout = NullWriter()", source)
        self.assertIn("ThreadSelectiveStdout(prior_stdout)", source)
        self.assertIn("while not live_stop.wait(1):", source)
        self.assertIn("live_stop.set()", source)
        self.assertIn("thread.join(timeout=5)", source)
        self.assertIn("silence_current_thread()", source)
        self.assertIn("finally:\n                live_daemon_off()", source)
        self.assertIn("conf.stop_watching()", source)
        # Nothing in this suite invokes privileged policy functions.
        self.assertTrue(any(isinstance(n, ast.FunctionDef) and
                            n.name == "main" for n in module.body))


if __name__ == "__main__":
    unittest.main(verbosity=2)
