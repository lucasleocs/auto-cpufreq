#!/usr/bin/env python3
"""Run the bounded PR965 harness without requiring project dependencies first."""

import sys
from types import ModuleType

requests_stub = ModuleType("requests")
requests_stub.RequestException = Exception
sys.modules.setdefault("requests", requests_stub)

import pr965_regression_harness_v3 as harness

raise SystemExit(harness.main())
