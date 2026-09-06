#!/usr/bin/env python3
"""Focused tests for isolation of the reproducible Vitis launcher."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


VITIS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VITIS_DIR))

import vitis_flow  # noqa: E402


class ToolEnvironmentTests(unittest.TestCase):
    def test_conda_and_generic_build_settings_are_removed(self) -> None:
        source = {
            "HOME": "/home/tester",
            "PATH": "/opt/conda/envs/dsp/bin:/usr/local/bin:/usr/bin",
            "CONDA_PREFIX": "/opt/conda/envs/dsp",
            "CONDA_EXE": "/opt/conda/bin/conda",
            "CONDA_DEFAULT_ENV": "dsp",
            "_CE_CONDA": "",
            "VIRTUAL_ENV": "/opt/venv",
            "LDFLAGS": "-L/opt/conda/envs/dsp/lib",
            "CMAKE_PREFIX_PATH": "/opt/conda/envs/dsp",
            "CC": "host-gcc",
        }

        result = vitis_flow.clean_tool_environment(source)

        self.assertEqual(result["HOME"], "/home/tester")
        self.assertEqual(result["PATH"], "/usr/local/bin:/usr/bin")
        self.assertNotIn("CONDA_PREFIX", result)
        self.assertNotIn("CONDA_EXE", result)
        self.assertNotIn("VIRTUAL_ENV", result)
        self.assertNotIn("LDFLAGS", result)
        self.assertNotIn("CMAKE_PREFIX_PATH", result)
        self.assertNotIn("CC", result)


if __name__ == "__main__":
    unittest.main()
