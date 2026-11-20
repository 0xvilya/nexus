"""Record real Python test outcomes in an atomic, machine-readable report."""
import argparse
import os
import sys
import time
import unittest

import bootstrap
from rainforge_recovery import atomic_json

parser = argparse.ArgumentParser()
parser.add_argument("--native", action="store_true")
args = parser.parse_args()
if args.native:
    os.environ["RF_NATIVE_TEST"] = "1"
suite = unittest.defaultTestLoader.discover(str(bootstrap.ROOT / "tests"))
started = time.monotonic()
result = unittest.TextTestRunner(verbosity=2).run(suite)
report = {"tests_run": result.testsRun, "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
          "failures": [{"test": str(t), "traceback": msg} for t, msg in result.failures + result.errors],
          "skipped": [{"test": str(t), "reason": msg} for t, msg in result.skipped],
          "native_requested": args.native, "elapsed_seconds": round(time.monotonic() - started, 3),
          "successful": result.wasSuccessful()}
atomic_json(bootstrap.ROOT / "work/python-results.json", report)
sys.exit(0 if result.wasSuccessful() else 1)
