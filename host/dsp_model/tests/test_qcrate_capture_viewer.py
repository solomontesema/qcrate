#!/usr/bin/env python3
"""Focused tests for Q-Crate raw DSP capture analysis."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


MODEL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODEL_DIR))

import qcrate_capture_viewer as viewer  # noqa: E402
import qcrate_dsp as dsp  # noqa: E402
import qcrate_dsp_reference as deployed_reference  # noqa: E402


CONFIG = MODEL_DIR / "configs" / "tone_1mhz.json"


class DecodeTests(unittest.TestCase):
    def test_packed_iq_sign_extension(self) -> None:
        words = np.asarray(
            [0x00010001, 0x7FFF8000, 0x8000FFFF], dtype="<u4"
        )
        capture = viewer.decode_payload(words.tobytes())
        np.testing.assert_array_equal(capture.i_raw, [1, -32768, -1])
        np.testing.assert_array_equal(capture.q_raw, [1, 32767, -32768])

    def test_rejects_empty_and_partial_words(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty"):
            viewer.decode_payload(b"")
        with self.assertRaisesRegex(ValueError, "multiple of 4"):
            viewer.decode_payload(b"abc")


class AnalysisTests(unittest.TestCase):
    def test_complex_fft_detects_positive_tone(self) -> None:
        sample_rate = 12_500_000.0
        frequency = 1_000_000.0
        indexes = np.arange(1000)
        samples = 0.25 * np.exp(2j * np.pi * frequency * indexes / sample_rate)
        actual_hz, actual_dbfs = viewer.dominant_frequency(samples, sample_rate)
        self.assertAlmostEqual(actual_hz, frequency, places=6)
        self.assertAlmostEqual(actual_dbfs, 20.0 * np.log10(0.25), places=5)

    def test_reference_capture_has_no_mismatches(self) -> None:
        words = np.asarray(
            deployed_reference.generate_words(CONFIG, dsp.TABLE_DIR, 64),
            dtype="<u4",
        )
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.bin"
            capture_path.write_bytes(words.tobytes())
            actual, expected, modeled_input, summary = viewer.build_report(
                capture_path, CONFIG
            )
        np.testing.assert_array_equal(actual.words, expected.words)
        self.assertEqual(summary["words"], 64)
        self.assertEqual(summary["reference_mismatches"], 0)
        self.assertEqual(len(modeled_input), 1024)


if __name__ == "__main__":
    unittest.main()
