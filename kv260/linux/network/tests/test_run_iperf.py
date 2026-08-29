#!/usr/bin/env python3
"""Focused tests for construction of the iperf3 acceptance sweep."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


NETWORK_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NETWORK_DIR))

import run_iperf  # noqa: E402


class MeasurementTests(unittest.TestCase):
    def test_builds_forward_and_reverse_sweep(self) -> None:
        tests = run_iperf.measurements("192.0.2.20", 30, 3, 1400, ["100M", "500M"])
        self.assertEqual(
            [test.name for test in tests],
            [
                "tcp-host-to-kv260",
                "tcp-kv260-to-host",
                "udp-host-to-kv260-100M",
                "udp-kv260-to-host-100M",
                "udp-host-to-kv260-500M",
                "udp-kv260-to-host-500M",
            ],
        )
        self.assertNotIn("-R", tests[0].arguments)
        self.assertIn("-R", tests[1].arguments)
        self.assertEqual(tests[2].arguments[-5:], ("-u", "-b", "100M", "-l", "1400"))
        self.assertEqual(tests[3].arguments[-1], "-R")


if __name__ == "__main__":
    unittest.main()
