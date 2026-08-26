#!/usr/bin/env python3
"""Bounded PR965 harness with secure staging regression coverage."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

requests_stub = ModuleType("requests")
requests_stub.RequestException = Exception
sys.modules.setdefault("requests", requests_stub)

import pr965_regression_harness_v3 as harness


def test_update_keeps_secure_staging_directory_until_clone_starts():
    update = harness.load_update_module()

    def fake_run(cmd, *args, **kwargs):
        destination = Path(cmd[-1])
        assert destination.is_dir(), (
            "secure mkdtemp staging directory was removed before git clone started"
        )
        (destination / "auto-cpufreq-installer").write_text("#!/bin/sh\n")
        return SimpleNamespace(returncode=0, stderr="")

    update.run = fake_run
    with tempfile.TemporaryDirectory() as tmp:
        source = update.prepare_release_source(tmp, "v9.9.9")
        assert source.is_dir()


harness.TESTS.insert(2, test_update_keeps_secure_staging_directory_until_clone_starts)
raise SystemExit(harness.main())
