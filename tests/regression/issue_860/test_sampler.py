import csv
import importlib.util
import io
import os
import unittest
from pathlib import Path


PATH = Path(__file__).resolve().with_name("sample_process.py")
SPEC = importlib.util.spec_from_file_location("_issue860_sampler", PATH)
SAMPLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SAMPLER)


class ProcessSamplerTests(unittest.TestCase):
    def test_reads_own_process(self):
        pid = os.getpid()
        current = SAMPLER.snapshot(pid)
        self.assertGreaterEqual(current["ticks"], 0)
        self.assertGreaterEqual(current["rss_mib"], 0)
        self.assertGreaterEqual(current["threads"], 1)
        self.assertIn(pid, current["task_ticks"])
        self.assertEqual(
            current["ticks"],
            SAMPLER.cpu_ticks(Path(f"/proc/{pid}/stat").read_text()),
        )

    def test_samples_without_privileged_access(self):
        output = io.StringIO()
        SAMPLER.sample(os.getpid(), 0.1, 2, output)
        output.seek(0)
        rows = list(csv.DictReader(output))
        self.assertEqual(len(rows), 2)
        self.assertEqual(int(rows[0]["pid"]), os.getpid())
        self.assertGreaterEqual(float(rows[0]["cpu_percent_one_core"]), 0.0)
        self.assertGreaterEqual(float(rows[1]["rss_mib"]), 0.0)

    def test_missing_pid(self):
        with self.assertRaises(FileNotFoundError):
            SAMPLER.snapshot(2147483647)


if __name__ == "__main__":
    unittest.main(verbosity=2)
