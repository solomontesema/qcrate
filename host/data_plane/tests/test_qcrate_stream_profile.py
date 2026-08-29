#!/usr/bin/env python3
"""Keep the deployed DSP stream identity tied to its canonical sources."""
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "host" / "dsp_model" / "configs" / "tone_1mhz.json"
TABLE_MANIFEST = ROOT / "rtl" / "dsp" / "tables" / "manifest.json"
PROFILE_HEADER = ROOT / "common" / "data_plane" / "qcrate_stream_profiles.h"


class StreamProfileTests(unittest.TestCase):
    def test_dsp_config_id_matches_tracked_sources(self) -> None:
        digest = hashlib.sha256(CONFIG.read_bytes() + TABLE_MANIFEST.read_bytes()).hexdigest()
        match = re.search(
            r"QCRATE_DSP_CONFIG_ID\s+UINT64_C\(0x([0-9a-f]{16})\)",
            PROFILE_HEADER.read_text(),
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), digest[:16])


if __name__ == "__main__":
    unittest.main()
