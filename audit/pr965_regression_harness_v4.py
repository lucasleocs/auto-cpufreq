#!/usr/bin/env python3
"""Run the bounded PR965 harness without requiring project dependencies first."""

import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

requests_stub = ModuleType("requests")
requests_stub.RequestException = Exception
sys.modules.setdefault("requests", requests_stub)

import pr965_regression_harness_v3 as harness

raise SystemExit(harness.main())
