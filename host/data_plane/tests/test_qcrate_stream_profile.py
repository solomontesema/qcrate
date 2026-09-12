#!/usr/bin/env python3
"""Keep deployed DSP stream identity tied to captured runtime state."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PROFILE_HEADER = ROOT / "common" / "data_plane" / "qcrate_stream_profiles.h"
STREAMER = ROOT / "kv260" / "linux" / "data_plane" / "qcrate_streamer.c"
DMA_UAPI = ROOT / "common" / "dma" / "qcrate_dma_uapi.h"


class StreamProfileTests(unittest.TestCase):
    def test_dsp_identity_is_not_a_compiled_stream_constant(self) -> None:
        self.assertNotIn("QCRATE_DSP_CONFIG_ID", PROFILE_HEADER.read_text())

    def test_streamer_requires_hardware_captured_identity(self) -> None:
        source = STREAMER.read_text()
        self.assertIn("QCRATE_DMA_CAP_CAPTURE_CONFIG_ID", source)
        self.assertIn("bank->captured_config_id != context->config_id", source)
        self.assertIn("capture.captured_config_id != config_id", source)
        self.assertIn("QCRATE_DMA_CAP_CAPTURE_CONFIG_ID", DMA_UAPI.read_text())


if __name__ == "__main__":
    unittest.main()
