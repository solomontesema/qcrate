#!/usr/bin/env python3
"""Keep the deployed DSP stream identity tied to its canonical sources."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RESOLVED_PROFILE = (
    ROOT
    / "host"
    / "experiment_profiles"
    / "examples"
    / "resolved"
    / "lo_29mhz.resolved.json"
)
PROFILE_HEADER = ROOT / "common" / "data_plane" / "qcrate_stream_profiles.h"


class StreamProfileTests(unittest.TestCase):
    def test_dsp_config_id_matches_tracked_sources(self) -> None:
        profile = json.loads(RESOLVED_PROFILE.read_text(encoding="utf-8"))
        expected = profile["identity"]["dsp_config_id"].removeprefix("0x")
        match = re.search(
            r"QCRATE_DSP_CONFIG_ID\s+UINT64_C\(0x([0-9a-f]{16})\)",
            PROFILE_HEADER.read_text(),
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), expected)


if __name__ == "__main__":
    unittest.main()
