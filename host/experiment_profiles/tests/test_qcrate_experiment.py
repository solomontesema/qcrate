#!/usr/bin/env python3
"""Focused orchestration tests for the DP-6D experiment runner."""
from __future__ import annotations

import argparse
import shlex
import sys
import unittest
from pathlib import Path


PROFILE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROFILE_DIR))

import qcrate_experiment as experiment  # noqa: E402
import qcrate_profile as profile  # noqa: E402


RESOLVED = PROFILE_DIR / "examples" / "resolved" / "lo_28_5mhz.resolved.json"


class ExperimentCommandTests(unittest.TestCase):
    def test_remote_workload_is_profile_bound_and_ordered(self) -> None:
        resolved = profile.load_resolved_profile(RESOLVED)
        args = argparse.Namespace(
            destination="192.0.2.10", port=47000, shots=10, banks=4,
            rate_mbps=420, sequence="/home/petalinux/qcrate/demo.qseq",
        )
        command = experiment.remote_workload(
            resolved, args, "/tmp/report.json", "BEGIN", "END"
        )
        self.assertIn(resolved["sequence"]["image_sha256"], command)
        self.assertIn("0xaf46287bb969ed24", command)
        self.assertIn("--words 1024 --frames 4", command)
        self.assertLess(command.index("sha256sum"), command.index("config-apply"))
        self.assertLess(command.index("config-apply"), command.index("qcrate-sequence load"))
        self.assertLess(command.index("qcrate-sequence load"), command.index("qcrate-streamer"))

    def test_configuration_command_remains_shell_round_trip_safe(self) -> None:
        resolved = profile.load_resolved_profile(RESOLVED)
        command = profile.configuration_command(resolved, "qcrate-control")
        self.assertEqual(shlex.split(shlex.join(command)), command)


if __name__ == "__main__":
    unittest.main()
